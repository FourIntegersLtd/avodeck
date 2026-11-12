"""Slice A, audit 15 (BG-08, BG-07): `POST /process` returns the live bundle job; "Try again" has a lifetime cap.

- Two requests while a `process_bundle` job is queued, running (heartbeat recent) or paused to resume itself
  give the same `job_id` and one row. A finished job, or one paused for a person (cancelled), is not live: the
  next request queues a new job.
- `POST /process/retry` answers 409 `retry_limit` once the only failed job has used its 5 person retries.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import Harness, harness
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def h(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
    ) as running:
        yield running


def bundle_job_count(h: Harness) -> int:
    row = h.admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'process_bundle'", (h.case_id,)
    ).fetchone()
    assert row is not None
    return int(row[0])


async def start(h: Harness) -> uuid.UUID:
    sent = await h.client.post(f"/v1/cases/{h.case_id}/process")
    assert sent.status_code == 202, sent.status_code
    return uuid.UUID(sent.json()["job_id"])


async def test_two_requests_while_a_job_is_queued_give_one_job(h: Harness) -> None:
    first = await start(h)
    assert await start(h) == first
    assert bundle_job_count(h) == 1


async def test_a_running_job_is_returned_too(h: Harness) -> None:
    first = await start(h)
    h.admin.execute("update app.jobs set status = 'running' where id = %s", (first,))
    assert await start(h) == first
    assert bundle_job_count(h) == 1


async def test_a_job_paused_to_resume_itself_is_returned(h: Harness) -> None:
    first = await start(h)
    h.admin.execute("update app.jobs set status = 'running' where id = %s", (first,))
    h.admin.execute("update app.jobs set status = 'paused', error_code = 'service_busy' where id = %s", (first,))
    assert await start(h) == first
    assert bundle_job_count(h) == 1


async def test_after_the_job_finishes_a_new_request_queues_a_new_job(h: Harness) -> None:
    first = await start(h)
    h.admin.execute("update app.jobs set status = 'running' where id = %s", (first,))
    h.admin.execute("update app.jobs set status = 'done', finished_at = now() where id = %s", (first,))
    second = await start(h)
    assert second != first
    assert bundle_job_count(h) == 2


async def test_a_job_the_expert_cancelled_is_not_live(h: Harness) -> None:
    first = await start(h)
    h.admin.execute("update app.jobs set status = 'running' where id = %s", (first,))
    h.admin.execute("update app.jobs set status = 'paused', error_code = 'cancelled' where id = %s", (first,))
    assert await start(h) != first


async def test_a_running_job_with_no_recent_heartbeat_is_an_orphan_and_not_live(h: Harness) -> None:
    first = await start(h)
    h.admin.execute("update app.jobs set status = 'running' where id = %s", (first,))
    h.admin.execute("alter table app.jobs disable trigger jobs_guard")  # the guard stamps updated_at on every change
    h.admin.execute("update app.jobs set updated_at = now() - interval '2 hours' where id = %s", (first,))
    h.admin.execute("alter table app.jobs enable trigger jobs_guard")
    assert await start(h) != first


async def test_retry_answers_409_retry_limit_once_the_job_used_its_retries(h: Harness) -> None:
    job = await h.enqueue("process_bundle")
    h.admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    h.admin.execute("update app.jobs set status = 'failed', error_code = 'job_error' where id = %s", (job,))
    for _ in range(5):
        assert (await h.client.post(f"/v1/cases/{h.case_id}/process/retry")).json() == {"requeued": 1}
        h.admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
        h.admin.execute("update app.jobs set status = 'failed', error_code = 'job_error' where id = %s", (job,))
    got = await h.client.post(f"/v1/cases/{h.case_id}/process/retry")
    assert got.status_code == 409, got.status_code
    assert got.json()["error"]["code"] == "retry_limit"
    assert h.admin.execute("select status from app.jobs where id = %s", (job,)).fetchone() == ("failed",)
