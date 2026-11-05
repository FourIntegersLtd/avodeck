"""Stall requeues are capped (scalability plan PR 5 leftover).

A stall is not a failure, so a stalled job is put back without an attempt (migration 0058). A job that stalls
every time (a memory kill on every run) would loop for ever. After `JOB_MAX_STALL_REQUEUES` stall requeues
(default 5) the next stall ends the job `failed` with the code `stalled_too_often` through the usual
dead-letter path (one `job_dead_lettered` Sentry event with kind and id only) instead of putting it back.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app.core import logs
from app.core.config import ConfigError
from app.core.db import Database
from app.core.jobs import JobContext
from app.worker.main import build_worker
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ENV_EXAMPLE = Path(__file__).resolve().parents[2] / ".env.example"


class Capture(Transport):
    sent: list[bytes] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        Capture.sent.append(envelope.serialize())


@pytest.fixture
def sentry() -> Iterator[list[bytes]]:
    Capture.sent = []
    logs.init_sentry("https://public@sentry.invalid/1", environment="test", transport=Capture)
    try:
        yield Capture.sent
    finally:
        sentry_sdk.init(dsn=None)


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def dead_letters(sent: list[bytes]) -> list[str]:
    sentry_sdk.flush()
    return [b.decode() for b in sent if b"job_dead_lettered" in b]


def procrastinate_admin(admin_dsn: str) -> psycopg.Connection:
    return psycopg.connect(admin_dsn, autocommit=True, options="-c search_path=procrastinate")


def stall(admin_dsn: str, queue: str) -> None:
    """A worker takes the queued row, then stops heartbeating."""
    with procrastinate_admin(admin_dsn) as q:
        dead = q.execute("select procrastinate_register_worker_v1()").fetchone()[0]  # type: ignore[index]
        q.execute("select id from procrastinate_fetch_job_v2(%s, %s)", ([queue], dead))
        q.execute("update procrastinate_workers set last_heartbeat = now() - interval '1 hour' where id = %s", (dead,))


def queue_status(admin_dsn: str, job: uuid.UUID) -> str:
    with procrastinate_admin(admin_dsn) as q:
        found = q.execute("select status from procrastinate_jobs where args ->> 'job_id' = %s", (str(job),)).fetchone()
    assert found is not None
    return str(found[0])


def job_row(admin: psycopg.Connection, job: uuid.UUID) -> tuple[Any, ...]:
    found = admin.execute(
        "select status, attempts, error_code, stall_requeues from app.jobs where id = %s", (job,)
    ).fetchone()
    assert found is not None
    return found


async def enqueue(api_db: Database, seed: Seed, registry: Any, kind: str) -> uuid.UUID:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    async with api_db.request(scope_for(expert)) as conn:
        return await registry.enqueue(conn, case, kind)  # type: ignore[no-any-return]


def test_the_cap_defaults_to_five_and_is_read_from_the_environment() -> None:
    env = {
        "DATABASE_URL": "postgresql://app_api:x@127.0.0.1:1/postgres",
        "DATABASE_URL_DIRECT": "postgresql://app_worker:x@127.0.0.1:1/postgres",
    }
    assert build_worker(env, features="app.features").max_stall_requeues == 5
    env["JOB_MAX_STALL_REQUEUES"] = "2"
    assert build_worker(env, features="app.features").max_stall_requeues == 2
    env["JOB_MAX_STALL_REQUEUES"] = "often"
    with pytest.raises(ConfigError):
        build_worker(env, features="app.features")
    env["JOB_MAX_STALL_REQUEUES"] = "-1"
    with pytest.raises(ConfigError):
        build_worker(env, features="app.features")


def test_the_cap_is_documented_in_the_env_example() -> None:
    assert "JOB_MAX_STALL_REQUEUES=5" in ENV_EXAMPLE.read_text()


async def test_each_stall_up_to_the_cap_is_counted_and_requeued_without_an_attempt(
    api_db: Database, worker_db: Database, worker_dsn: str, admin_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()

    @registry.kind("zz_stall_counted")
    async def never(ctx: JobContext) -> None:
        raise AssertionError("not run in this test")

    job = await enqueue(api_db, seed, registry, "zz_stall_counted")
    admin.execute("update app.jobs set status = 'running', attempts = 1, started_at = now() where id = %s", (job,))
    async with open_worker(registry, worker_db, worker_dsn, max_stall_requeues=2) as worker:
        for n in (1, 2):
            stall(admin_dsn, registry.queue)
            assert await worker.requeue_stalled() == 1
            assert job_row(admin, job) == ("running", 1, None, n)
            assert queue_status(admin_dsn, job) == "todo"


async def test_a_stall_past_the_cap_fails_the_job_once_instead_of_requeueing_it(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin_dsn: str,
    admin: psycopg.Connection,
    seed: Seed,
    sentry: list[bytes],
    caplog: pytest.LogCaptureFixture,
) -> None:
    registry = isolated_registry()
    ran: list[int] = []

    @registry.kind("zz_stall_capped")
    async def handler(ctx: JobContext) -> None:
        ran.append(ctx.attempt)

    job = await enqueue(api_db, seed, registry, "zz_stall_capped")
    admin.execute("update app.jobs set status = 'running', attempts = 1, started_at = now() where id = %s", (job,))
    caplog.set_level(logging.DEBUG)
    async with open_worker(registry, worker_db, worker_dsn, max_stall_requeues=2) as worker:
        for _ in (1, 2):
            stall(admin_dsn, registry.queue)
            assert await worker.requeue_stalled() == 1
        stall(admin_dsn, registry.queue)
        assert await worker.requeue_stalled() == 0, "the third stall is not put back"

        status, attempts, code, _ = job_row(admin, job)
        assert (status, attempts, code) == ("failed", 1, "stalled_too_often")
        assert admin.execute("select finished_at is not null from app.jobs where id = %s", (job,)).fetchone() == (True,)
        assert queue_status(admin_dsn, job) == "failed", "the queue row has ended, so no worker takes it again"

        # a later sweep leaves the ended job alone, and nothing runs it
        assert await worker.requeue_stalled() == 0
        await worker.requeue_orphaned()  # other tests' jobs share this database: only this job is checked
    assert ran == []
    assert job_row(admin, job)[0] == "failed"

    events = dead_letters(sentry)
    assert len(events) == 1
    assert "zz_stall_capped" in events[0] and str(job) in events[0]
    assert "stalled_too_often" not in events[0], "the event carries kind and id only"
    for record in caplog.records:  # rule 6: ids, kinds and counts only
        assert "zz_stall_capped" not in record.getMessage() or str(job) in record.getMessage()


async def test_a_cap_of_zero_never_requeues_a_stalled_job(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin_dsn: str,
    admin: psycopg.Connection,
    seed: Seed,
    sentry: list[bytes],
) -> None:
    registry = isolated_registry()

    @registry.kind("zz_stall_zero")
    async def handler(ctx: JobContext) -> None:
        raise AssertionError("never run")

    job = await enqueue(api_db, seed, registry, "zz_stall_zero")
    admin.execute("update app.jobs set status = 'running', attempts = 1, started_at = now() where id = %s", (job,))
    async with open_worker(registry, worker_db, worker_dsn, max_stall_requeues=0) as worker:
        stall(admin_dsn, registry.queue)
        assert await worker.requeue_stalled() == 0
    assert job_row(admin, job)[:3] == ("failed", 1, "stalled_too_often")
    assert len(dead_letters(sentry)) == 1


async def test_a_job_under_the_cap_still_finishes_and_a_default_worker_allows_five_stalls(
    api_db: Database, worker_db: Database, worker_dsn: str, admin_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    registry = isolated_registry()
    seen: list[int] = []

    @registry.kind("zz_stall_then_ok")
    async def handler(ctx: JobContext) -> None:
        seen.append(ctx.attempt)

    job = await enqueue(api_db, seed, registry, "zz_stall_then_ok")
    admin.execute("update app.jobs set status = 'running', attempts = 1, started_at = now() where id = %s", (job,))
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert worker.max_stall_requeues == 5
        for _ in range(5):
            stall(admin_dsn, registry.queue)
            assert await worker.requeue_stalled() == 1
        await run_until_finished(worker, admin, [job])
    assert seen == [1]
    assert job_row(admin, job)[:3] == ("done", 1, None)
