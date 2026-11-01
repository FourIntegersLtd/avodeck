"""The Bedrock slot scheduler (`AiKit._wait_for_slot`, migration 0049): coordinates every worker
job's calls against the account-wide on-demand throughput ceiling (not raisable per tenant), with
round-robin fairness so one job cannot starve another by polling faster.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.ai.kit import AiKit
from app.core.db import Database
from tests.support.ai import cell
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


async def _pending_row(admin: psycopg.Connection, job_id: uuid.UUID) -> tuple[object, ...] | None:
    return admin.execute(
        "select id, granted_at from app.ai_request_slots where job_id = %s", (job_id,)
    ).fetchone()


async def test_a_job_waits_when_the_sixty_second_ceiling_is_already_spent(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    other_a, other_b = seed.job(case), seed.job(case)
    waiting = seed.job(case)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at)"
        " values (%s, %s, now(), now()), (%s, %s, now(), now())",
        (other_a, case, other_b, case),
    )
    admin.commit()

    async def fake_sleep(_seconds: float) -> None:
        raise _Stop()

    kit = AiKit(cell(), object(), sleep=fake_sleep)  # type: ignore[arg-type]
    kit.settings = type(kit.settings)(**{**kit.settings.__dict__, "bedrock_rpm_limit": 2})

    async with worker_db.job(case) as conn:
        with pytest.raises(_Stop):
            await kit._wait_for_slot(conn, waiting)

    row = await _pending_row(admin, waiting)
    assert row is not None and row[1] is None  # registered as waiting, not granted

    status = admin.execute("select step from app.jobs where id = %s", (waiting,)).fetchone()
    assert status == ("waiting_for_capacity",)


async def test_a_job_is_granted_once_capacity_frees(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    waiting = seed.job(case)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at) values (%s, %s, now(), now())",
        (waiting, case),
    )
    # A grant from over sixty seconds ago no longer counts against the ceiling.
    stale_job = seed.job(case)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at)"
        " values (%s, %s, now() - interval '90 seconds', now() - interval '90 seconds')",
        (stale_job, case),
    )
    admin.commit()

    kit = AiKit(cell(), object())  # type: ignore[arg-type]
    kit.settings = type(kit.settings)(**{**kit.settings.__dict__, "bedrock_rpm_limit": 2})

    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, waiting)

    row = await _pending_row(admin, waiting)
    assert row is not None and row[1] is not None


async def test_detached_a_job_waits_when_the_sixty_second_ceiling_is_already_spent(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    other_a, other_b = seed.job(case), seed.job(case)
    waiting = seed.job(case)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at)"
        " values (%s, %s, now(), now()), (%s, %s, now(), now())",
        (other_a, case, other_b, case),
    )
    admin.commit()

    async def fake_sleep(_seconds: float) -> None:
        raise _Stop()

    kit = AiKit(cell(), object(), sleep=fake_sleep)  # type: ignore[arg-type]
    kit.settings = type(kit.settings)(**{**kit.settings.__dict__, "bedrock_rpm_limit": 2})

    with pytest.raises(_Stop):
        await kit._wait_for_slot_detached(worker_db, case, waiting)

    row = await _pending_row(admin, waiting)
    assert row is not None and row[1] is None

    status = admin.execute("select step from app.jobs where id = %s", (waiting,)).fetchone()
    assert status == ("waiting_for_capacity",)


async def test_detached_a_job_is_granted_once_capacity_frees(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    waiting = seed.job(case)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at) values (%s, %s, now(), now())",
        (waiting, case),
    )
    stale_job = seed.job(case)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at)"
        " values (%s, %s, now() - interval '90 seconds', now() - interval '90 seconds')",
        (stale_job, case),
    )
    admin.commit()

    kit = AiKit(cell(), object())  # type: ignore[arg-type]
    kit.settings = type(kit.settings)(**{**kit.settings.__dict__, "bedrock_rpm_limit": 2})

    await kit._wait_for_slot_detached(worker_db, case, waiting)

    row = await _pending_row(admin, waiting)
    assert row is not None and row[1] is not None


async def test_detached_round_robin_fairness(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    job_old = seed.job(case)
    job_new = seed.job(case)
    admin.execute("update app.jobs set last_granted_at = now() - interval '5 minutes' where id = %s", (job_old,))
    admin.execute("update app.jobs set last_granted_at = now() - interval '1 second' where id = %s", (job_new,))
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at) values (%s, %s, now())", (job_old, case)
    )
    admin.commit()

    async def fake_sleep(_seconds: float) -> None:
        raise _Stop()

    kit = AiKit(cell(), object(), sleep=fake_sleep)  # type: ignore[arg-type]
    kit.settings = type(kit.settings)(**{**kit.settings.__dict__, "bedrock_rpm_limit": 5})

    with pytest.raises(_Stop):
        await kit._wait_for_slot_detached(worker_db, case, job_new)
    new_row = await _pending_row(admin, job_new)
    assert new_row is not None and new_row[1] is None

    admin.execute("update app.ai_request_slots set granted_at = now() where job_id = %s", (job_old,))
    admin.execute("update app.jobs set last_granted_at = now() where id = %s", (job_old,))
    admin.commit()

    kit.sleep = fake_sleep
    await kit._wait_for_slot_detached(worker_db, case, job_new)
    new_row = await _pending_row(admin, job_new)
    assert new_row is not None and new_row[1] is not None


async def test_round_robin_fairness_the_longer_waiting_job_goes_first(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    job_old = seed.job(case)
    job_new = seed.job(case)
    # job_old has been granted a slot before, long ago; job_new was granted one moments ago.
    admin.execute("update app.jobs set last_granted_at = now() - interval '5 minutes' where id = %s", (job_old,))
    admin.execute("update app.jobs set last_granted_at = now() - interval '1 second' where id = %s", (job_new,))
    # job_old is already registered as waiting for its next slot.
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at) values (%s, %s, now())", (job_old, case)
    )
    admin.commit()

    kit = AiKit(cell(), object())  # type: ignore[arg-type]
    kit.settings = type(kit.settings)(**{**kit.settings.__dict__, "bedrock_rpm_limit": 5})

    async def fake_sleep(_seconds: float) -> None:
        raise _Stop()

    kit.sleep = fake_sleep

    # Plenty of capacity, but job_old (older last_granted_at) is pending, so job_new must not jump ahead.
    async with worker_db.job(case) as conn:
        with pytest.raises(_Stop):
            await kit._wait_for_slot(conn, job_new)
    new_row = await _pending_row(admin, job_new)
    assert new_row is not None and new_row[1] is None

    # Once job_old's own wait is granted (by another worker tick), job_new is the oldest pending job left.
    admin.execute("update app.ai_request_slots set granted_at = now() where job_id = %s", (job_old,))
    admin.execute("update app.jobs set last_granted_at = now() where id = %s", (job_old,))
    admin.commit()

    kit.sleep = fake_sleep  # would raise _Stop again if the kit still chose to wait
    async with worker_db.job(case) as conn:
        await kit._wait_for_slot(conn, job_new)
    new_row = await _pending_row(admin, job_new)
    assert new_row is not None and new_row[1] is not None
