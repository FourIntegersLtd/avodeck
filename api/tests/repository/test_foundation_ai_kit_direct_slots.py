"""TEMPORARY, dummy data only (ADR 0036): the slot gate reads `settings.rpm_limit`, so it follows the
primary provider: the direct API's limit when it is primary, `BEDROCK_RPM_LIMIT` otherwise.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.ai.kit import AiKit
from app.core.db import Database
from tests.support.ai import cell, cell_env
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


def _spent(admin: psycopg.Connection, seed: Seed, case: uuid.UUID, grants: int) -> None:
    for _ in range(grants):
        admin.execute(
            "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at)"
            " values (%s, %s, now(), now())",
            (seed.job(case), case),
        )
    admin.commit()


def _kit(**changes: object) -> AiKit:
    async def stop(_seconds: float) -> None:
        raise _Stop()

    from app.core.config import ai_settings

    settings = ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test-not-real"))
    kit = AiKit(settings, object(), sleep=stop)  # type: ignore[arg-type]
    kit.settings = dataclasses.replace(settings, **changes)
    return kit


async def test_with_the_direct_api_primary_the_gate_uses_its_limit_not_bedrocks(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    _spent(admin, seed, case, 5)  # Bedrock's limit of 5 is spent, the direct limit of 30 is not
    waiting = seed.job(case)
    admin.commit()
    async with worker_db.job(case) as conn:
        await _kit()._wait_for_slot(conn, waiting)  # granted at once: no sleep, so no _Stop
    row = admin.execute("select granted_at from app.ai_request_slots where job_id = %s", (waiting,)).fetchone()
    assert row is not None and row[0] is not None


async def test_with_the_direct_api_primary_the_gate_still_holds_at_its_own_limit(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    _spent(admin, seed, case, 2)
    waiting = seed.job(case)
    admin.commit()
    async with worker_db.job(case) as conn:
        with pytest.raises(_Stop):
            await _kit(anthropic_rpm_limit=2)._wait_for_slot(conn, waiting)
    with pytest.raises(_Stop):
        await _kit(anthropic_rpm_limit=2)._wait_for_slot_detached(worker_db, case, waiting)


async def test_with_bedrock_primary_the_gate_is_unchanged(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    _spent(admin, seed, case, 5)
    waiting = seed.job(case)
    admin.commit()

    async def stop(_seconds: float) -> None:
        raise _Stop()

    kit = AiKit(cell(), object(), sleep=stop)  # type: ignore[arg-type]
    assert kit.settings.rpm_limit == 5
    async with worker_db.job(case) as conn:
        with pytest.raises(_Stop):
            await kit._wait_for_slot(conn, waiting)
