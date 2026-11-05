"""Migration 0060: a job change tells listeners which case moved (PR 8, live updates pushed not polled).

The notification carries the case id and nothing else (rule 6): a uuid, never a step name, a count or text.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest

from tests.support.seed import Seed

CHANNEL = "case_jobs"


@pytest.fixture
def listener(admin_dsn: str) -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(admin_dsn, autocommit=True)
    conn.execute(f"listen {CHANNEL}")
    yield conn
    conn.close()


def _drain(conn: psycopg.Connection, wait: float = 1.0) -> list[str]:
    return [n.payload for n in conn.notifies(timeout=wait)]


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    person = seed.person()
    org = seed.org(kind="team", members={person: "owner"})
    return seed.case(org, person)


def test_a_new_job_notifies_with_exactly_the_case_id(
    seed: Seed, listener: psycopg.Connection, case: uuid.UUID
) -> None:
    _drain(listener, 0.2)
    seed.job(case)
    assert _drain(listener) == [str(case)]


@pytest.mark.parametrize(
    "assignment",
    [
        "status = 'running'",
        "step = 'ocr'",
        "pct = 40",
        "step_done = 3, step_total = 9",
        "error_code = 'some_code'",
    ],
)
def test_each_progress_column_change_notifies_with_a_uuid_payload(
    seed: Seed, admin: psycopg.Connection, listener: psycopg.Connection, case: uuid.UUID, assignment: str
) -> None:
    job = seed.job(case)
    _drain(listener, 0.3)
    admin.execute(f"update app.jobs set {assignment} where id = %s", (job,))  # noqa: S608 - fixed test strings
    payloads = _drain(listener)
    assert payloads == [str(case)]
    assert str(uuid.UUID(payloads[0])) == payloads[0], "the payload is exactly a uuid"


def test_a_change_to_a_column_the_stream_does_not_show_does_not_notify(
    seed: Seed, admin: psycopg.Connection, listener: psycopg.Connection, case: uuid.UUID
) -> None:
    job = seed.job(case)
    _drain(listener, 0.3)
    admin.execute("update app.jobs set attempts = attempts + 1, code_version = 'v1' where id = %s", (job,))
    assert _drain(listener, 0.5) == []


def test_a_job_without_a_case_does_not_notify(admin: psycopg.Connection, listener: psycopg.Connection) -> None:
    _drain(listener, 0.2)
    job = uuid.uuid4()
    admin.execute("insert into app.jobs (id, case_id, kind) values (%s, null, 'process_bundle')", (job,))
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    assert _drain(listener, 0.5) == []
