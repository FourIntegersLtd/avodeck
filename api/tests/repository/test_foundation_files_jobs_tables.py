"""Migration 0007: uploads, job steps, scheduled runs, transcripts and the Procrastinate queue schema.

- `uploads` follow their state machine (open -> complete) and are visible only to people who may upload.
- `jobs.step` and `jobs.error_code` hold short codes only, never text (rule 6).
- The queue's own schema is the worker's: the API role cannot read or write it directly, only queue
  through `app.enqueue_job()`.
- Scheduled runs are written only through the worker's claim function, keyed by name and date.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def roles(api_dsn: str, worker_dsn: str) -> dict[str, str]:
    return {"app_api": api_dsn, "app_worker": worker_dsn}


def as_role(conn: psycopg.Connection, **settings: str) -> None:
    for name, value in settings.items():
        conn.execute("select set_config(%s, %s, true)", (name.replace("__", "."), value))


@pytest.fixture
def case_with_upload(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, outsider = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    upload = uuid.uuid4()
    admin.execute(
        "insert into app.uploads (id, case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count)"
        " values (%s, %s, %s, 'records', 'f.pdf', 10, %s, 8388608, 1)",
        (upload, case, expert, "0" * 64),
    )
    return {"expert": expert, "outsider": outsider, "case": case, "upload": upload}


def test_an_upload_is_visible_only_to_someone_who_may_upload_to_its_case(
    roles: dict[str, str], case_with_upload: dict[str, uuid.UUID]
) -> None:
    for person, expected in ((case_with_upload["expert"], 1), (case_with_upload["outsider"], 0)):
        with psycopg.connect(roles["app_api"]) as conn:
            as_role(conn, app__person_id=str(person))
            count = conn.execute(
                "select count(*) from app.uploads where id = %s", (case_with_upload["upload"],)
            ).fetchone()
            assert count == (expected,)


def test_upload_status_follows_its_state_machine(
    admin: psycopg.Connection, case_with_upload: dict[str, uuid.UUID]
) -> None:
    upload = case_with_upload["upload"]
    admin.execute("update app.uploads set status = 'complete' where id = %s", (upload,))
    with pytest.raises(psycopg.Error) as caught:
        admin.execute("update app.uploads set status = 'open' where id = %s", (upload,))
    assert caught.value.sqlstate == "AV409"


@pytest.mark.parametrize("column", ["step", "error_code"])
def test_job_step_and_error_code_hold_short_codes_not_text(seed: Seed, admin: psycopg.Connection, column: str) -> None:
    expert = seed.person()
    job = seed.job(seed.case(seed.org(kind="personal", members={expert: "owner"}), expert))
    admin.execute(f"update app.jobs set {column} = 'ocr_pages' where id = %s", (job,))  # noqa: S608 - fixed names
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(f"update app.jobs set {column} = 'Seen by Dr Example on 3 May' where id = %s", (job,))  # noqa: S608


def test_the_api_role_cannot_touch_the_queue_schema_directly(roles: dict[str, str]) -> None:
    with psycopg.connect(roles["app_api"]) as conn:
        as_role(conn)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select count(*) from procrastinate.procrastinate_jobs")


def test_the_worker_role_uses_the_queue_schema(roles: dict[str, str]) -> None:
    with psycopg.connect(roles["app_worker"]) as conn:
        as_role(conn)
        conn.execute("select count(*) from procrastinate.procrastinate_jobs")


def test_scheduled_runs_are_written_only_through_the_claim_function(roles: dict[str, str]) -> None:
    name = f"zz_daily_{uuid.uuid4().hex[:8]}"
    for role in ("app_api", "app_worker"):
        with psycopg.connect(roles[role]) as conn:
            as_role(conn)
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("insert into app.scheduled_runs (name, run_date) values (%s, current_date)", (name,))
    with psycopg.connect(roles["app_api"]) as conn:
        as_role(conn)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select app.claim_scheduled_run(%s, current_date)", (name,))
    with psycopg.connect(roles["app_worker"]) as conn:
        as_role(conn)
        assert conn.execute("select app.claim_scheduled_run(%s, current_date)", (name,)).fetchone() == (True,)
        assert conn.execute("select app.claim_scheduled_run(%s, current_date)", (name,)).fetchone() == (False,)


def test_a_transcript_is_private_to_its_speaker(seed: Seed, admin: psycopg.Connection, roles: dict[str, str]) -> None:
    expert, assistant = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    case = seed.case(org, expert, members={assistant: "assistant"})
    admin.execute(
        "insert into app.transcripts (case_id, person_id, context, text, duration_seconds)"
        " values (%s, %s, 'opinion', %s, 5)",
        (case, expert, b"sealed-bytes-not-text"),
    )
    for person, expected in ((expert, 1), (assistant, 0)):
        with psycopg.connect(roles["app_api"]) as conn:
            as_role(conn, app__person_id=str(person))
            assert conn.execute("select count(*) from app.transcripts where case_id = %s", (case,)).fetchone() == (
                expected,
            )
