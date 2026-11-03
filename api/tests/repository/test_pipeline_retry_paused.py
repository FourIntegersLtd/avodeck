"""Slice A: a paused "Try these pages again" run is not a dead end.

A pause that is not resumable (the cost guard, the key service unreachable) never resumes by itself. The bundle
summary says why in a fixed code (`retry.pause_reason`: `cost_guard` or `other`, never text), and pressing "Try
again" hands the same paused job back to the queue (`app.resume_paused_job()`, migration 0133): never a second job,
never a second queue row. The job stays `paused` with the short code `resuming` until the worker picks it up, so
the summary shows it as running at once. If the cost guard pauses it again, the summary says so again.
"""

# ruff: noqa: F811 (the fixtures are imported, then named by each test)
from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from tests.repository.test_pipeline_retry_unread import RETRY, world  # noqa: F401

pytestmark = pytest.mark.anyio


def paused_retry(admin: psycopg.Connection, case: uuid.UUID, reason: str = "cost_guard") -> uuid.UUID:
    """A retry run the worker paused with `reason` (a job starts queued, then runs, then pauses)."""
    row = admin.execute("insert into app.jobs (case_id, kind) values (%s, %s) returning id", (case, RETRY)).fetchone()
    assert row is not None
    admin.execute("update app.jobs set status = 'running' where id = %s", (row[0],))
    admin.execute("update app.jobs set status = 'paused', error_code = %s where id = %s", (reason, row[0]))
    return row[0]  # type: ignore[no-any-return]


def state(admin: psycopg.Connection, job: uuid.UUID) -> tuple[str, str | None]:
    row = admin.execute("select status, error_code from app.jobs where id = %s", (job,)).fetchone()
    assert row is not None
    return row


def queue_rows(admin: psycopg.Connection, job: uuid.UUID) -> int:
    row = admin.execute(
        "select count(*) from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(job),)
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


async def retry_of(client: Any, case: uuid.UUID) -> dict[str, Any]:
    got = (await client.get(f"/v1/cases/{case}/bundle/summary")).json()["retry"]
    assert got is not None
    return got  # type: ignore[no-any-return]


async def test_the_summary_says_why_a_run_paused_in_a_fixed_code(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any
) -> None:
    client = await client_for(world["expert"])
    job = paused_retry(admin, world["case"], "cost_guard")
    got = await retry_of(client, world["case"])
    assert (got["running"], got["paused"], got["pause_reason"]) == (False, True, "cost_guard")
    admin.execute("update app.jobs set error_code = 'key_service_unreachable' where id = %s", (job,))
    got = await retry_of(client, world["case"])
    assert (got["paused"], got["pause_reason"]) == (True, "other")


async def test_a_run_that_is_not_paused_has_no_pause_reason(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any
) -> None:
    client = await client_for(world["expert"])
    admin.execute("insert into app.jobs (case_id, kind) values (%s, %s)", (world["case"], RETRY))
    got = await retry_of(client, world["case"])
    assert (got["running"], got["paused"], got["pause_reason"]) == (True, False, None)


async def test_pressing_try_again_hands_the_paused_run_back_to_the_queue_once(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    job = paused_retry(admin, case, "cost_guard")
    for _ in range(2):  # a second press changes nothing
        resp = await client.post(f"/v1/cases/{case}/transcript/retry")
        assert resp.status_code == 202 and resp.json()["status"] == "preparing"
    jobs = admin.execute("select count(*) from app.jobs where case_id = %s and kind = %s", (case, RETRY)).fetchone()
    assert jobs == (1,), "never a second job"
    assert queue_rows(admin, job) == 1, "never a second queue row"
    assert state(admin, job) == ("paused", "resuming")  # the worker makes paused -> running when it picks it up


async def test_a_run_handed_back_shows_as_running_until_the_worker_picks_it_up(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    paused_retry(admin, case, "cost_guard")
    await client.post(f"/v1/cases/{case}/transcript/retry")
    got = await retry_of(client, case)
    assert (got["running"], got["paused"], got["pause_reason"]) == (True, False, None)


async def test_a_run_paused_again_says_why_again(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    job = paused_retry(admin, case, "cost_guard")
    await client.post(f"/v1/cases/{case}/transcript/retry")
    # The worker picks it up (paused -> running, code cleared) and the cost guard pauses it again at once.
    admin.execute("update app.jobs set status = 'running', error_code = null where id = %s", (job,))
    admin.execute("update app.jobs set status = 'paused', error_code = 'cost_guard' where id = %s", (job,))
    got = await retry_of(client, case)
    assert (got["running"], got["paused"], got["pause_reason"]) == (False, True, "cost_guard")
    assert (await client.post(f"/v1/cases/{case}/transcript/retry")).status_code == 202  # and it can be pressed again
    assert state(admin, job) == ("paused", "resuming")


async def test_nobody_without_access_to_the_case_can_hand_a_paused_run_back(
    world: dict[str, Any], admin: psycopg.Connection
) -> None:
    job = paused_retry(admin, world["case"], "cost_guard")
    row = admin.execute("select app.resume_paused_job(%s, 'default', 0, true)", (job,)).fetchone()  # no person
    assert row == (False,)
    assert state(admin, job) == ("paused", "cost_guard") and queue_rows(admin, job) == 0


async def test_a_run_on_a_case_on_hold_is_not_handed_back(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    job = paused_retry(admin, case, "cost_guard")
    admin.execute("update app.cases set on_hold = true where id = %s", (case,))
    await client.post(f"/v1/cases/{case}/transcript/retry")
    assert state(admin, job) == ("paused", "cost_guard") and queue_rows(admin, job) == 0
