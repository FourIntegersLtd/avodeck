"""Background jobs, audit 15, area B: who can put work on the queue, read it, change it or tamper with it.

Already covered (not repeated here):
- a stranger enqueueing on another person's case: `test_foundation_tenant_isolation_db_layer.py::
  test_l9_enqueue_job_for_the_victims_case_is_refused_and_adds_no_job`;
- a stranger reading a job, its events, cancelling or retrying processing (every route, 404 and nothing of the
  victim in the body): `test_foundation_tenant_isolation_http_routes.py`;
- a worker scoped to one case seeing or changing another's rows in every case table, and a signed-in API connection
  using the worker's case key: `test_foundation_tenant_isolation_db_layer.py` sections g, h and i.

New here: the roles WITHIN one case (a viewer, an assistant), the queue tables themselves, what the restricted API
role may call, and what the worker role can do in the queue's schema.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from tests.support.auth import APP_ORIGIN
from tests.support.background_jobs import attempt, defer, queue_rows, role_connection
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

INSUFFICIENT_PRIVILEGE = "42501"


@pytest.fixture
def auth_env() -> dict[str, str]:
    """Requests here carry a seeded device session; the sign-in service is never called, so no service is needed."""
    dummy = {"SUPABASE_URL": "http://127.0.0.1:9", "SUPABASE_ANON_KEY": "zz", "SUPABASE_SERVICE_ROLE_KEY": "zz"}
    return {**dummy, "APP_ORIGIN": APP_ORIGIN}


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    return build_app(api_db, auth_env)


class Team:
    def __init__(self, seed: Seed) -> None:
        self.expert, self.assistant, self.viewer, self.outsider = (seed.person() for _ in range(4))
        org = seed.org(kind="team", members={self.expert: "owner"})
        seed.org(kind="personal", members={self.outsider: "owner"})
        self.case = seed.case(org, self.expert, members={self.assistant: "assistant", self.viewer: "viewer"})


@pytest.fixture
def team(seed: Seed) -> Team:
    return Team(seed)


@pytest.fixture
def as_api(api_dsn: str) -> Iterator[Any]:
    """`as_api(person)` -> a restricted API connection with the person set, rolled back at the end."""
    stack: list[Any] = []

    def make(person: uuid.UUID | None) -> psycopg.Connection:
        ctx = role_connection(api_dsn)
        conn = ctx.__enter__()
        stack.append(ctx)
        if person is not None:
            conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        return conn

    yield make
    for ctx in reversed(stack):
        ctx.__exit__(None, None, None)


# --- 4. a role inside the case --------------------------------------------------------------------------------------


def test_the_upload_role_can_enqueue_processing_but_a_viewer_cannot(
    team: Team, as_api: Any, admin: psycopg.Connection
) -> None:
    code, rows, _ = attempt(
        as_api(team.assistant), "select app.enqueue_job(%s, 'process_bundle', 'zz_q', 0, false, 'x')", (team.case,)
    )
    assert (code, rows) == (None, 1), "control: the legitimate role enqueues"
    code, _, _ = attempt(
        as_api(team.viewer), "select app.enqueue_job(%s, 'process_bundle', 'zz_q', 0, false, 'x')", (team.case,)
    )
    assert code == INSUFFICIENT_PRIVILEGE
    assert admin.execute("select count(*) from app.jobs where case_id = %s", (team.case,)).fetchone() == (
        0,
    )  # rolled back


@pytest.mark.parametrize(("kind", "priority"), [("export_case", 10), ("delete_case", 0)])
def test_enqueue_needs_the_action_of_the_kind_not_just_upload(
    team: Team, as_api: Any, kind: str, priority: int
) -> None:
    """`app.job_kind_actions` (0015): nobody queues an export or a deletion with the action of an upload.
    The priority is the one the kind has (an interactive kind 10, BG-09): the database refuses any other."""
    own = attempt(
        as_api(team.expert), "select app.enqueue_job(%s, %s, 'zz_q', %s, false, 'x')", (team.case, kind, priority)
    )
    assert own[0] is None, "control: the role that holds the action enqueues it"
    refused = attempt(
        as_api(team.assistant), "select app.enqueue_job(%s, %s, 'zz_q', %s, false, 'x')", (team.case, kind, priority)
    )
    assert refused[0] == INSUFFICIENT_PRIVILEGE


async def test_a_viewer_reads_a_job_but_cannot_cancel_or_retry_processing_and_nothing_changes(
    client_for: ClientFor, team: Team, seed: Seed, admin: psycopg.Connection
) -> None:
    live = seed.job(team.case)  # queued bundle job
    failed = seed.job(team.case)
    admin.execute("update app.jobs set status = 'running' where id = %s", (failed,))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error' where id = %s", (failed,))
    viewer = await client_for(team.viewer)
    assert (await viewer.get(f"/v1/jobs/{live}")).status_code == 200  # control: the viewer may see progress
    cancel = await viewer.post(f"/v1/cases/{team.case}/process/cancel")
    retry = await viewer.post(f"/v1/cases/{team.case}/process/retry")
    assert (cancel.status_code, retry.status_code) == (403, 403), (
        cancel.text,
        retry.text,
    )  # sees the case, lacks the action
    assert admin.execute("select count(*) from app.case_processing where case_id = %s", (team.case,)).fetchone() == (0,)
    assert admin.execute("select status from app.jobs where id = %s", (failed,)).fetchone() == ("failed",)
    assert queue_rows(admin, failed) == []
    # control: the upload role does both on the same case
    assistant = await client_for(team.assistant)
    assert (await assistant.post(f"/v1/cases/{team.case}/process/cancel")).json() == {"cancelled": True}
    again = await assistant.post(f"/v1/cases/{team.case}/process/retry")
    assert again.status_code == 200 and again.json()["requeued"] == 1, again.text
    assert admin.execute("select status from app.jobs where id = %s", (failed,)).fetchone() == ("queued",)
    assert [s for _, s in queue_rows(admin, failed)] == ["todo"]


async def test_an_outsider_sees_no_job_and_changes_nothing(
    client_for: ClientFor, team: Team, seed: Seed, admin: psycopg.Connection
) -> None:
    """A single check beside the viewer's, to read the two together (the full route list is in the isolation file)."""
    job = seed.job(team.case)
    outsider = await client_for(team.outsider)
    assert (await outsider.get(f"/v1/jobs/{job}")).status_code == 404
    assert (await outsider.post(f"/v1/cases/{team.case}/process/cancel")).status_code == 404
    assert admin.execute("select count(*) from app.case_processing where case_id = %s", (team.case,)).fetchone() == (0,)


# --- 5. the restricted API role and the queue -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "statement",
    [
        "select count(*) from procrastinate.procrastinate_jobs",
        "select count(*) from procrastinate.procrastinate_events",
        "select count(*) from procrastinate.procrastinate_workers",
        "insert into procrastinate.procrastinate_jobs (queue_name, task_name, args) values ('zz_q', 'run_job', '{}')",
        "update procrastinate.procrastinate_jobs set status = 'failed'",
        "delete from procrastinate.procrastinate_jobs",
        "select procrastinate.procrastinate_defer_jobs_v1(array[]::procrastinate.procrastinate_job_to_defer_v1[])",
    ],
)
def test_the_api_role_has_no_access_to_the_queues_own_schema(team: Team, as_api: Any, statement: str) -> None:
    code, _, _ = attempt(as_api(team.expert), statement)
    assert code == INSUFFICIENT_PRIVILEGE, statement


def test_control_the_legitimate_enqueue_does_reach_the_queue(
    team: Team, api_dsn: str, admin: psycopg.Connection
) -> None:
    """Without this the refusals above could mean nothing reaches the queue at all."""
    with psycopg.connect(api_dsn, autocommit=False) as conn:
        conn.execute("select set_config('app.person_id', %s, true)", (str(team.expert),))
        job = conn.execute(
            "select app.enqueue_job(%s, 'process_bundle', 'zz_q', 0, true, 'x')", (team.case,)
        ).fetchone()
        conn.commit()
    assert job is not None
    assert queue_rows(admin, job[0]) == [("zz_q", "todo")]
    assert admin.execute(
        "select lock from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(job[0]),)
    ).fetchone() == (f"case:{team.case}",)


@pytest.mark.parametrize("function", ["requeue_orphaned_jobs(3, 0)", "job_waits_for_hold(gen_random_uuid(), false)"])
def test_the_api_role_cannot_call_the_workers_job_functions(
    team: Team, as_api: Any, worker_dsn: str, function: str
) -> None:
    code, _, _ = attempt(as_api(team.expert), f"select app.{function}")
    assert code == INSUFFICIENT_PRIVILEGE, function
    with role_connection(worker_dsn) as worker:  # control: the worker role may call both
        assert attempt(worker, f"select app.{function}")[0] is None, function


def test_the_api_role_cannot_change_or_delete_a_jobs_row_even_on_its_own_case(
    team: Team, as_api: Any, seed: Seed, admin: psycopg.Connection
) -> None:
    job = seed.job(team.case)
    conn = as_api(team.expert)
    assert attempt(conn, "select id from app.jobs where id = %s", (job,))[2] == [(job,)], "control: it sees the row"
    for statement in (
        "update app.jobs set status = 'failed', attempts = 99 where id = %s",
        "delete from app.jobs where id = %s",
    ):
        code, rows, _ = attempt(conn, statement, (job,))
        assert (code is not None) or rows == 0, statement
    assert admin.execute("select status, attempts from app.jobs where id = %s", (job,)).fetchone() == ("queued", 0)


def test_the_api_role_cannot_insert_a_job_row_in_another_persons_case(
    team: Team, as_api: Any, seed: Seed, admin: psycopg.Connection
) -> None:
    other_expert = seed.person()
    other_case = seed.case(seed.org(kind="personal", members={other_expert: "owner"}), other_expert)
    code, _, _ = attempt(
        as_api(team.expert), "insert into app.jobs (case_id, kind) values (%s, 'process_bundle')", (other_case,)
    )
    assert code == INSUFFICIENT_PRIVILEGE
    assert admin.execute("select count(*) from app.jobs where case_id = %s", (other_case,)).fetchone() == (0,)


@pytest.mark.parametrize("kind", ["export_case", "delete_case"])
def test_a_direct_insert_into_jobs_cannot_choose_a_kind_the_role_may_not_enqueue(
    team: Team, as_api: Any, kind: str
) -> None:
    code, rows, _ = attempt(
        as_api(team.assistant), "insert into app.jobs (case_id, kind) values (%s, %s)", (team.case, kind)
    )
    assert code == INSUFFICIENT_PRIVILEGE, f"the upload role inserted a {kind} job directly ({rows} row)"


def test_the_orphan_sweep_does_not_queue_a_job_row_inserted_around_enqueue_job(
    team: Team, api_dsn: str, worker_dsn: str, admin: psycopg.Connection
) -> None:
    with psycopg.connect(api_dsn, autocommit=False) as conn:
        conn.execute("select set_config('app.person_id', %s, true)", (str(team.assistant),))
        try:
            row = conn.execute(
                "insert into app.jobs (case_id, kind, priority) values (%s, 'export_case', -100000) returning id",
                (team.case,),
            ).fetchone()
            conn.commit()
        except psycopg.Error:
            row = None  # refused: the safe outcome
    if row is None:
        return  # refused: the safe outcome
    time.sleep(0.05)
    # The sweep runs in a transaction that is rolled back, so it queues nothing for any other test's leftover rows;
    # what it queued for THIS job is read before the rollback.
    with role_connection(worker_dsn) as worker:
        worker.execute("select app.requeue_orphaned_jobs(3, 0)")
        queued = queue_rows(worker, row[0])
    assert queued == [], "the sweep queued a job the role could not have enqueued"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="BG-04: app.requeue_failed_job checks only case.upload, for any kind and any queue name, not the kind's"
    " action from app.job_kind_actions",
)
def test_requeue_failed_job_needs_the_action_of_the_kind(
    team: Team, seed: Seed, as_api: Any, admin: psycopg.Connection
) -> None:
    export = seed.job(team.case, kind="export_case", requested_by=team.expert)
    admin.execute("update app.jobs set status = 'running' where id = %s", (export,))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error' where id = %s", (export,))
    code, _, rows = attempt(as_api(team.assistant), "select app.requeue_failed_job(%s, 'zz_q', 0, false)", (export,))
    assert code == INSUFFICIENT_PRIVILEGE or rows == [(False,)], f"the upload role requeued a failed export: {rows}"


def test_control_requeue_failed_job_for_a_processing_job_works_for_the_upload_role(
    team: Team, seed: Seed, as_api: Any, admin: psycopg.Connection
) -> None:
    job = seed.job(team.case)
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error' where id = %s", (job,))
    assert attempt(as_api(team.assistant), "select app.requeue_failed_job(%s, 'zz_q', 0, false)", (job,))[2] == [
        (True,)
    ]
    assert attempt(as_api(team.viewer), "select app.requeue_failed_job(%s, 'zz_q', 0, false)", (job,))[2] == [(False,)]


# --- 6. the worker role --------------------------------------------------------------------------------------------


@pytest.fixture
def two_cases(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID]:
    a, b = seed.person(), seed.person()
    case_a = seed.case(seed.org(kind="personal", members={a: "owner"}), a)
    case_b = seed.case(seed.org(kind="personal", members={b: "owner"}), b)
    return case_a, seed.job(case_a, requested_by=a), case_b, seed.job(case_b, requested_by=b)


def _worker_for(conn: psycopg.Connection, case: uuid.UUID) -> psycopg.Connection:
    conn.execute("select set_config('app.job_case_id', %s, true)", (str(case),))
    return conn


def test_a_worker_scoped_to_one_case_sees_and_changes_only_that_cases_jobs(
    worker_dsn: str, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID], admin: psycopg.Connection
) -> None:
    case_a, job_a, case_b, job_b = two_cases
    with role_connection(worker_dsn) as conn:
        _worker_for(conn, case_a)
        assert attempt(conn, "select id from app.jobs where id = any(%s)", ([job_a, job_b],))[2] == [(job_a,)]
        assert attempt(conn, "update app.jobs set pct = 5 where id = %s", (job_a,))[1] == 1, "control: its own job"
        for statement in (
            "update app.jobs set pct = 5 where id = %s",
            "delete from app.jobs where id = %s",
            "insert into app.job_steps (job_id, case_id, step) select id, case_id, 'zz_step' from app.jobs where id = %s",  # noqa: E501
        ):
            code, rows, _ = attempt(conn, statement, (job_b,))
            assert code is not None or rows == 0, statement
        assert (
            attempt(
                conn, "insert into app.job_steps (job_id, case_id, step) values (%s, %s, 'zz_step')", (job_b, case_b)
            )[0]
            == INSUFFICIENT_PRIVILEGE
        ), "a step row for another case's job is refused"
    assert admin.execute("select pct from app.jobs where id = %s", (job_b,)).fetchone() == (None,)


def test_documented_the_worker_role_has_full_rights_over_every_queue_row(
    worker_dsn: str, two_cases: tuple[uuid.UUID, uuid.UUID, uuid.UUID, uuid.UUID], admin: psycopg.Connection
) -> None:
    """By design the queue (`procrastinate.*`) is not case-scoped: the worker role reads, rewrites, deletes and adds
    queue rows of every case, whatever case key it has set. The case key protects `app.*` only. This pins it so a
    change (for example a queue role with narrower rights) is noticed. Whoever holds the worker credential can
    therefore re-point, drop or invent jobs; see the audit report (the credential is separate from the API's)."""
    case_a, job_a, case_b, job_b = two_cases
    for case, job in ((case_a, job_a), (case_b, job_b)):
        defer(admin, "zz_q", job, case)
    with role_connection(worker_dsn) as conn:
        _worker_for(conn, case_a)
        conn.execute("set local search_path = procrastinate, public")  # as the queue's own connections have it
        seen = attempt(
            conn,
            "select args ->> 'case_id' from procrastinate.procrastinate_jobs where args ->> 'job_id' = any(%s)",
            ([str(job_a), str(job_b)],),
        )
        assert sorted(r[0] for r in seen[2]) == sorted([str(case_a), str(case_b)]), "case B's queue row is visible"
        assert (
            attempt(
                conn,
                "update procrastinate.procrastinate_jobs set status = 'failed' where args ->> 'job_id' = %s",
                (str(job_b),),
            )[1]
            == 1
        )
        assert (
            attempt(conn, "delete from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s", (str(job_b),))[1]
            == 1
        )
        assert (
            attempt(
                conn,
                "insert into procrastinate.procrastinate_jobs (queue_name, task_name, args) values ('zz_q', 'run_job', '{}')",  # noqa: E501
            )[0]
            is None
        )
