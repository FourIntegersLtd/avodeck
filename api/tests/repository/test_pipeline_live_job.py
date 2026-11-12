"""Slice A, BG-08: which `process_bundle` job counts as live (`repo.live_bundle_job`), without the HTTP layer.

Queued, running with a recent heartbeat and paused to resume by itself are live; finished, failed, cancelled and an
orphaned `running` row (no heartbeat for 2 hours) are not.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from app.core.jobs import RESUMABLE_PAUSES
from app.features.pipeline import repo
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def _live(api_db: Database, expert: uuid.UUID, case: uuid.UUID) -> uuid.UUID | None:
    async with api_db.request(scope_for(expert)) as conn:
        return await repo.live_bundle_job(conn, case, sorted(RESUMABLE_PAUSES))


def _move(admin: psycopg.Connection, job: uuid.UUID, *steps: tuple[str, str | None]) -> None:
    for status, code in steps:
        admin.execute("update app.jobs set status = %s, error_code = %s where id = %s", (status, code, job))


@pytest.mark.parametrize(
    ("steps", "live"),
    [
        ((), True),
        ((("running", None),), True),
        ((("running", None), ("paused", "service_busy")), True),
        ((("running", None), ("paused", "model_unavailable")), True),
        ((("running", None), ("paused", "resuming")), True),
        ((("running", None), ("paused", "cancelled")), False),
        ((("running", None), ("paused", "cost_guard")), False),
        ((("running", None), ("failed", "job_error")), False),
        ((("running", None), ("done", None)), False),
    ],
)
async def test_which_states_count_as_live(
    api_db: Database, admin: psycopg.Connection, seed: Seed, steps: tuple[tuple[str, str | None], ...], live: bool
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = seed.job(case, requested_by=expert)
    _move(admin, job, *steps)
    assert await _live(api_db, expert, case) == (job if live else None)


async def test_a_running_job_with_no_heartbeat_for_two_hours_is_not_live(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    job = seed.job(case, requested_by=expert)
    _move(admin, job, ("running", None))
    admin.execute("alter table app.jobs disable trigger jobs_guard")  # the guard stamps updated_at on every change
    admin.execute("update app.jobs set updated_at = now() - interval '2 hours' where id = %s", (job,))
    admin.execute("alter table app.jobs enable trigger jobs_guard")
    assert await _live(api_db, expert, case) is None


async def test_another_case_and_another_kind_are_not_live(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case, other = seed.case(org, expert), seed.case(org, expert)
    seed.job(other, requested_by=expert)
    seed.job(case, kind="repair_fact_dates", requested_by=expert)
    assert await _live(api_db, expert, case) is None
