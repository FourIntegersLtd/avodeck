"""Support's job actions (support-account plan phase 3; permissions.md "Avodeck staff"; state-machines.md "Job").

- `POST /v1/support/jobs/{job_id}/resume` (flag `support_job_resume`): a paused job goes back to the
  queue now; the worker moves it paused -> running when it picks it up. Any other status is 409
  `job_not_paused`; a case on hold is 409 `case_on_hold` (the hold keeps its jobs waiting).
- `POST /v1/support/jobs/{job_id}/requeue` (flag `support_job_requeue`): a failed job goes failed ->
  queued with its attempts back to 0; what it was (status, error code, attempts) stays in
  `job_support_actions`. Any other status is 409 `job_not_failed`.
- Both take `{reason_code}` from a fixed list and nothing else (rule 6), answer with ids and codes only,
  and send the named expert a patient-free email (the case and job ids). A job that does not exist, or
  has no case, is 404.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

RESUME, REQUEUE = "support_job_resume", "support_job_requeue"


@pytest.fixture
def staff(seed: Seed) -> uuid.UUID:
    person = seed.person()
    seed.support_staff(person)
    seed.flag(RESUME, "person", person)
    seed.flag(REQUEUE, "person", person)
    return person


@pytest.fixture
def case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return seed.case(org, expert), expert


def job_in(seed: Seed, admin: psycopg.Connection, case: uuid.UUID, status: str, *, error_code: str | None = None,
           attempts: int = 3) -> uuid.UUID:  # fmt: skip
    job = seed.job(case)
    path = {"queued": [], "running": ["running"], "done": ["running", "done"], "failed": ["running", "failed"],
            "paused": ["running", "paused"]}[status]  # fmt: skip
    for step in path:
        admin.execute(
            "update app.jobs set status = %s, attempts = %s, error_code = %s where id = %s",
            (step, attempts, error_code, job),
        )
    return job


def queue_rows(admin: psycopg.Connection, job: uuid.UUID) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select queue_name, status, lock from procrastinate.procrastinate_jobs where args->>'job_id' = %s"
        " order by id",
        (str(job),),
    ).fetchall()


def history(admin: psycopg.Connection, job: uuid.UUID) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select action, reason_code, from_status, error_code, attempts, person_id from app.job_support_actions"
        " where job_id = %s order by created_at",
        (job,),
    ).fetchall()


async def requeue(client_for: ClientFor, staff: uuid.UUID, job: uuid.UUID) -> Any:
    client = await client_for(staff)
    return await client.post(f"/v1/support/jobs/{job}/requeue", json={"reason_code": "fault_fixed"})


def job_row(admin: psycopg.Connection, job: uuid.UUID) -> tuple[Any, ...]:
    row = admin.execute("select status, attempts, error_code from app.jobs where id = %s", (job,)).fetchone()
    assert row is not None
    return row


async def test_resume_hands_a_paused_job_back_to_the_queue(
    client_for: ClientFor, app: FastAPI, admin: psycopg.Connection, seed: Seed, staff: uuid.UUID,
    case: tuple[uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    case_id, expert = case
    job = job_in(seed, admin, case_id, "paused", error_code="cost_guard", attempts=2)
    response = await (await client_for(staff)).post(
        f"/v1/support/jobs/{job}/resume", json={"reason_code": "service_restored"}
    )
    assert response.status_code == 200, response.text
    assert response.json() == {"job_id": str(job), "case_id": str(case_id), "action": "resume", "status": "paused"}
    assert job_row(admin, job) == ("paused", 2, "cost_guard")  # the worker moves it to running
    assert queue_rows(admin, job) == [("jobs", "todo", f"case:{case_id}")]
    assert history(admin, job) == [("resume", "service_restored", "paused", "cost_guard", 2, staff)]
    [mail] = app.state.mailer.outbox
    expert_email = admin.execute("select email from app.people where id = %s", (expert,)).fetchone()
    assert expert_email is not None
    assert (mail.to, mail.template, mail.params) == (
        expert_email[0], "support.job_resumed", {"case_ref": str(case_id), "job_ref": str(job)},
    )  # fmt: skip


async def test_resume_twice_does_not_queue_the_job_twice(
    client_for: ClientFor, admin: psycopg.Connection, seed: Seed, staff: uuid.UUID, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    job = job_in(seed, admin, case[0], "paused", error_code="service_busy")
    client = await client_for(staff)
    for _ in range(2):
        response = await client.post(f"/v1/support/jobs/{job}/resume", json={"reason_code": "service_restored"})
        assert response.status_code == 200, response.text
    assert [status for _, status, _ in queue_rows(admin, job)] == ["todo"]


@pytest.mark.parametrize("status", ["queued", "running", "done", "failed"])
async def test_resume_refuses_a_job_that_is_not_paused(
    status: str, client_for: ClientFor, app: FastAPI, admin: psycopg.Connection, seed: Seed, staff: uuid.UUID,
    case: tuple[uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    job = job_in(seed, admin, case[0], status)
    before = job_row(admin, job)
    response = await (await client_for(staff)).post(
        f"/v1/support/jobs/{job}/resume", json={"reason_code": "service_restored"}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (409, "job_not_paused")
    assert job_row(admin, job) == before and history(admin, job) == [] and queue_rows(admin, job) == []
    assert app.state.mailer.outbox == []


async def test_resume_waits_while_the_case_is_on_hold(
    client_for: ClientFor, admin: psycopg.Connection, seed: Seed, staff: uuid.UUID, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    job = job_in(seed, admin, case[0], "paused", error_code="cost_guard")
    admin.execute("update app.cases set on_hold = true where id = %s", (case[0],))
    response = await (await client_for(staff)).post(
        f"/v1/support/jobs/{job}/resume", json={"reason_code": "service_restored"}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (409, "case_on_hold")
    assert history(admin, job) == [] and queue_rows(admin, job) == []


async def test_requeue_puts_a_failed_job_back_with_its_attempts_reset_and_its_history_kept(
    client_for: ClientFor, app: FastAPI, admin: psycopg.Connection, seed: Seed, staff: uuid.UUID,
    case: tuple[uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    case_id, _ = case
    job = job_in(seed, admin, case_id, "failed", error_code="job_error", attempts=3)
    response = await requeue(client_for, staff, job)
    assert response.status_code == 200, response.text
    assert response.json() == {"job_id": str(job), "case_id": str(case_id), "action": "requeue", "status": "queued"}
    assert job_row(admin, job) == ("queued", 0, "job_error")
    assert queue_rows(admin, job) == [("jobs", "todo", f"case:{case_id}")]
    assert history(admin, job) == [("requeue", "fault_fixed", "failed", "job_error", 3, staff)]
    [mail] = app.state.mailer.outbox
    assert (mail.template, mail.params) == ("support.job_requeued", {"case_ref": str(case_id), "job_ref": str(job)})


@pytest.mark.parametrize("status", ["queued", "running", "done", "paused"])
async def test_requeue_refuses_a_job_that_has_not_failed(
    status: str, client_for: ClientFor, admin: psycopg.Connection, seed: Seed, staff: uuid.UUID,
    case: tuple[uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    job = job_in(seed, admin, case[0], status)
    before = job_row(admin, job)
    response = await requeue(client_for, staff, job)
    assert (response.status_code, response.json()["error"]["code"]) == (409, "job_not_failed")
    assert job_row(admin, job) == before and history(admin, job) == [] and queue_rows(admin, job) == []


@pytest.mark.parametrize("verb", ["resume", "requeue"])
async def test_a_job_that_does_not_exist_or_has_no_case_is_not_found(
    verb: str, client_for: ClientFor, admin: psycopg.Connection, staff: uuid.UUID
) -> None:
    caseless = uuid.uuid4()
    admin.execute("insert into app.jobs (id, case_id, kind) values (%s, null, 'zz_cell_job')", (caseless,))
    admin.execute("update app.jobs set status = 'running' where id = %s", (caseless,))
    admin.execute("update app.jobs set status = 'failed' where id = %s", (caseless,))
    client = await client_for(staff)
    for job in (uuid.uuid4(), caseless):
        response = await client.post(f"/v1/support/jobs/{job}/{verb}", json={"reason_code": "expert_asked"})
        assert response.status_code == 404, response.text


@pytest.mark.parametrize(
    "body",
    [{}, {"reason_code": "because the expert rang"}, {"reason_code": "fault_fixed", "note": "anything"}],
)
async def test_the_body_is_a_reason_code_from_the_list_and_nothing_else(
    body: dict[str, str], client_for: ClientFor, admin: psycopg.Connection, seed: Seed, staff: uuid.UUID,
    case: tuple[uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    job = job_in(seed, admin, case[0], "failed", error_code="job_error")
    response = await (await client_for(staff)).post(f"/v1/support/jobs/{job}/requeue", json=body)
    assert response.status_code == 422
    assert history(admin, job) == []


async def test_without_an_email_service_the_action_still_happens(
    client_for: ClientFor, app: FastAPI, admin: psycopg.Connection, seed: Seed, staff: uuid.UUID,
    case: tuple[uuid.UUID, uuid.UUID],
) -> None:  # fmt: skip
    class Unconfigured:
        async def send(self, mail: object) -> None:
            raise RuntimeError("no email service")

    app.state.mailer = Unconfigured()
    job = job_in(seed, admin, case[0], "failed", error_code="job_error")
    response = await requeue(client_for, staff, job)
    assert response.status_code == 200, response.text
    assert job_row(admin, job)[0] == "queued"
