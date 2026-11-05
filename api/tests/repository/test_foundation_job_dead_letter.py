"""A job that ends `failed` for good sends ONE Sentry event `job_dead_lettered` with its kind and id only."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
import sentry_sdk
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app.core import logs
from app.core.db import Database
from app.core.jobs import JobContext
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry, open_worker, run_until_finished
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


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


def events(sent: list[bytes]) -> list[str]:
    sentry_sdk.flush()
    return [b.decode() for b in sent if b"job_dead_lettered" in b]


async def test_a_job_failing_for_good_sends_one_event_with_kind_and_id_only(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    seed: Seed,
    sentry: list[bytes],
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case_id = seed.case(org, expert)
    registry = isolated_registry()

    @registry.kind("zz_dead")
    async def dead(ctx: JobContext) -> None:
        raise RuntimeError("synthetic patient text must not travel")

    async with api_db.request(scope_for(expert)) as conn:
        job = await registry.enqueue(conn, case_id, "zz_dead")
    async with open_worker(registry, worker_db, worker_dsn, max_attempts=2) as worker:
        await run_until_finished(worker, admin, [job])
    sent = events(sentry)
    assert len(sent) == 1
    wire = sent[0]
    assert "zz_dead" in wire and str(job) in wire
    assert str(case_id) not in wire
    assert "synthetic patient text" not in wire


async def test_a_job_that_recovers_sends_nothing(
    api_db: Database,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    seed: Seed,
    sentry: list[bytes],
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case_id = seed.case(org, expert)
    registry = isolated_registry()

    @registry.kind("zz_flaky_ok")
    async def flaky(ctx: JobContext) -> None:
        if ctx.attempt == 1:
            raise RuntimeError("synthetic")

    async with api_db.request(scope_for(expert)) as conn:
        job: uuid.UUID = await registry.enqueue(conn, case_id, "zz_flaky_ok")
    async with open_worker(registry, worker_db, worker_dsn, max_attempts=3) as worker:
        await run_until_finished(worker, admin, [job])
    assert events(sentry) == []
