"""TEMPORARY, dummy data only (ADR 0036): each model provider has its own slot pool (migration 0055).

A slot row says which provider it is for. The kit counts, grants and orders fairly within one provider
at a time, so the direct API's much larger ceiling never lets calls through to Bedrock's small one, and
Bedrock's full window never holds back the direct API.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.ai.kit import AiKit
from app.core.config import ai_settings
from app.core.db import Database
from tests.support.ai import cell_env
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class _Stop(Exception):
    """Raised by the fake sleep so a test can see that the kit chose to wait, without really waiting."""


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=6)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    return seed.case(org, expert)


def _kit() -> AiKit:
    async def stop(_seconds: float) -> None:
        raise _Stop()

    settings = ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test-not-real"))
    return AiKit(settings, object(), sleep=stop)  # type: ignore[arg-type]


def _spent(admin: psycopg.Connection, seed: Seed, case: uuid.UUID, provider: str, grants: int) -> None:
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at, provider)"
        " select %s, %s, now(), now(), %s from generate_series(1, %s)",
        (seed.job(case), case, provider, grants),
    )
    admin.commit()


def _provider_of(admin: psycopg.Connection, job: uuid.UUID) -> list[str]:
    rows = admin.execute("select provider from app.ai_request_slots where job_id = %s", (job,)).fetchall()
    return [r[0] for r in rows]


def test_the_kit_has_a_separate_limit_for_each_provider() -> None:
    limits = _kit().settings.limits
    assert limits["bedrock"].rpm == 5 and limits["anthropic"].rpm == 1000


async def test_a_full_direct_window_does_not_hold_back_bedrock(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    kit = _kit()
    _spent(admin, seed, case, "anthropic", kit.settings.limits["anthropic"].rpm)
    waiting = seed.job(case)
    admin.commit()
    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, waiting, "bedrock")  # granted at once: no sleep, so no _Stop
    assert _provider_of(admin, waiting) == ["bedrock"]


async def test_a_full_bedrock_window_does_not_hold_back_the_direct_api(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    kit = _kit()
    _spent(admin, seed, case, "bedrock", kit.settings.limits["bedrock"].rpm)
    waiting = seed.job(case)
    admin.commit()
    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, waiting, "anthropic")
    assert _provider_of(admin, waiting) == ["anthropic"]


async def test_each_pool_holds_at_its_own_limit(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    kit = _kit()
    _spent(admin, seed, case, "anthropic", kit.settings.limits["anthropic"].rpm)
    _spent(admin, seed, case, "bedrock", kit.settings.limits["bedrock"].rpm)
    waiting = seed.job(case)
    admin.commit()
    for provider in ("anthropic", "bedrock"):
        async with worker_db.job(case) as conn:
            with pytest.raises(_Stop):
                await kit._wait_for_slot(conn, waiting, provider)
    step = admin.execute("select step from app.jobs where id = %s", (waiting,)).fetchone()
    assert step is not None and step[0] == "waiting_for_capacity"


async def test_one_below_each_limit_is_still_granted(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    kit = _kit()
    _spent(admin, seed, case, "anthropic", kit.settings.limits["anthropic"].rpm - 1)
    waiting = seed.job(case)
    admin.commit()
    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, waiting, "anthropic")
    assert _provider_of(admin, waiting) == ["anthropic"]


async def test_the_detached_wait_uses_the_same_pools(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    kit = _kit()
    _spent(admin, seed, case, "bedrock", kit.settings.limits["bedrock"].rpm)
    waiting = seed.job(case)
    admin.commit()
    with pytest.raises(_Stop):
        await kit._wait_for_slot_detached(worker_db, case, waiting, "bedrock")
    await kit._wait_for_slot_detached(worker_db, case, waiting, "anthropic")
    assert "anthropic" in _provider_of(admin, waiting)


async def test_a_wait_with_no_provider_is_a_bedrock_wait(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    waiting = seed.job(case)
    admin.commit()
    async with worker_db.job(case) as conn:
        await _kit()._wait_for_slot(conn, waiting)
    assert _provider_of(admin, waiting) == ["bedrock"]


async def test_fair_order_is_kept_within_a_provider_not_across_them(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    older, newer = seed.job(case), seed.job(case)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, provider)"
        " values (%s, %s, now() - interval '1 minute', 'bedrock')",
        (older, case),
    )
    admin.commit()
    kit = _kit()
    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, newer, "anthropic")  # the older job queues for the other pool: not ahead of it
    assert _provider_of(admin, newer) == ["anthropic"]
    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, older, "bedrock")
    row = admin.execute(
        "select count(*) from app.ai_request_slots where job_id = %s and granted_at is not null", (older,)
    ).fetchone()
    assert row is not None and row[0] == 1


async def test_two_jobs_in_the_same_pool_are_still_served_in_fair_order(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    first, second = seed.job(case), seed.job(case)
    admin.execute("update app.jobs set last_granted_at = now() where id = %s", (first,))
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, provider)"
        " values (%s, %s, now(), 'anthropic')",
        (second, case),
    )
    admin.commit()
    kit = _kit()
    async with worker_db.job(case) as conn:
        with pytest.raises(_Stop):  # the job granted most recently waits for the one never granted
            await kit._wait_for_slot(conn, first, "anthropic")
    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, second, "anthropic")


async def test_a_slot_wait_logs_the_provider_the_grants_and_the_limit_then_the_grant(
    worker_db: Database,
    admin: psycopg.Connection,
    seed: Seed,
    case: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("DEBUG", logger="app.ai")
    settings = ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test-not-real"))
    full = settings.limits["bedrock"].rpm

    async def window_clears(_seconds: float) -> None:
        admin.execute(
            "update app.ai_request_slots set granted_at = now() - interval '2 minutes' where granted_at is not null"
        )
        admin.commit()

    kit = AiKit(settings, object(), sleep=window_clears)  # type: ignore[arg-type]
    _spent(admin, seed, case, "bedrock", full)
    waiting = seed.job(case)
    admin.commit()
    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, waiting, "bedrock")
    messages = [r.getMessage() for r in caplog.records]
    assert f"slot waiting: provider=bedrock grants_60s={full} limit={full}" in messages
    granted = [m for m in messages if m.startswith("slot granted:")]
    assert len(granted) == 1 and "provider=bedrock" in granted[0] and f"limit={full}" in granted[0]
