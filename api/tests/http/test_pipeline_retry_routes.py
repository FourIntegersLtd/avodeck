"""Slice A: "Try again" for failed processing (state-machines "Job": failed -> queued; failure-modes F5).

- `POST /v1/cases/{id}/process/retry` (case.upload) puts every failed processing job of the case back on
  the queue: status `queued`, no error code, attempts counted again from none, and the worker finishes it.
  The answer says how many were queued; a repeat with nothing failed is 200-ok with count 0.
- A viewer gets 403, anyone without access to the case 404, and nothing changes for them.
- Only the case's bundle jobs are touched; the same `Idempotency-Key` returns the first answer.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import Harness, harness
from tests.support.auth import CSRF_HEADER, cookie_header
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
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
    storage: SupabaseStorage,
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


async def failed_job(h: Harness, kind: str = "process_bundle", code: str = "service_busy") -> uuid.UUID:
    """A job that ran out of back-off: failed with the pause reason, no longer in the queue."""
    job = await h.enqueue(kind)
    h.admin.execute("set search_path = procrastinate, public")  # the queue's triggers use bare type names
    h.admin.execute(
        "update procrastinate.procrastinate_jobs set status = 'succeeded' where args->>'job_id' = %s", (str(job),)
    )
    h.admin.execute("reset search_path")
    h.admin.execute("update app.jobs set status = 'running', attempts = 2 where id = %s", (job,))
    h.admin.execute("update app.jobs set status = 'failed', error_code = %s where id = %s", (code, job))
    return job


def row(h: Harness, job: uuid.UUID) -> tuple[str, str | None, int]:
    return h.admin.execute("select status, error_code, attempts from app.jobs where id = %s", (job,)).fetchone()  # type: ignore[return-value]


def as_person(h: Harness, person: uuid.UUID) -> dict[str, str]:
    session = Seed(h.admin).session(person)
    return {"cookie": cookie_header(session), CSRF_HEADER: session.csrf}


async def test_failed_processing_goes_back_on_the_queue_and_finishes(h: Harness) -> None:
    job = await failed_job(h)
    got = await h.client.post(f"/v1/cases/{h.case_id}/process/retry")
    assert got.status_code == 200, got.status_code
    assert got.json() == {"requeued": 1}
    assert row(h, job) == ("queued", None, 0)
    assert h.admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'bundle.processing_retried'",
        (h.case_id,),
    ).fetchone() == (1,)
    assert (await h.drain())[job] == "done"


async def test_nothing_failed_is_ok_with_count_zero_and_a_repeat_changes_nothing(h: Harness) -> None:
    assert (await h.client.post(f"/v1/cases/{h.case_id}/process/retry")).json() == {"requeued": 0}
    job = await failed_job(h)
    assert (await h.client.post(f"/v1/cases/{h.case_id}/process/retry")).json() == {"requeued": 1}
    assert (await h.client.post(f"/v1/cases/{h.case_id}/process/retry")).json() == {"requeued": 0}
    assert row(h, job)[0] == "queued"


async def test_only_failed_processing_jobs_of_this_case_are_touched(h: Harness) -> None:
    unrelated = await failed_job(h, "repair_fact_dates")
    paused = await h.enqueue("process_bundle")
    h.admin.execute("update app.jobs set status = 'running' where id = %s", (paused,))
    h.admin.execute("update app.jobs set status = 'paused', error_code = 'cancelled' where id = %s", (paused,))
    got = await h.client.post(f"/v1/cases/{h.case_id}/process/retry")
    assert got.json() == {"requeued": 0}
    assert row(h, unrelated)[0] == "failed"
    assert row(h, paused)[0] == "paused"


async def test_the_same_idempotency_key_returns_the_first_answer(h: Harness) -> None:
    job = await failed_job(h)
    headers = {"Idempotency-Key": "zz-retry-once"}
    first = await h.client.post(f"/v1/cases/{h.case_id}/process/retry", headers=headers)
    second = await h.client.post(f"/v1/cases/{h.case_id}/process/retry", headers=headers)
    assert first.json() == second.json() == {"requeued": 1}
    assert second.headers.get("idempotent-replayed") == "true"
    assert row(h, job)[0] == "queued"


async def test_a_viewer_gets_403_and_anyone_else_404(h: Harness) -> None:
    job = await failed_job(h)
    viewer = Seed(h.admin).person()
    Seed(h.admin).case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    got = await h.client.post(f"/v1/cases/{h.case_id}/process/retry", headers=as_person(h, viewer))
    assert got.status_code == 403, got.status_code
    other = as_person(h, Seed(h.admin).person())
    got = await h.client.post(f"/v1/cases/{h.case_id}/process/retry", headers=other)
    assert got.status_code == 404, got.status_code
    assert row(h, job)[0] == "failed"
