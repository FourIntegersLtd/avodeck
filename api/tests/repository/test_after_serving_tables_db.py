"""Slice G's tables (migration 0700) under row-level security and their guards.

- Everyone on the case sees the after-serving rows; the expert and the assistant add deadlines,
  hearings and court directions (`case.edit_setup`); people outside the case see nothing.
- A deadline moves only with a `deadline_changes` row written in the same transaction (every move kept).
- Written questions and "may matter" rows are written by the worker only; an answered question keeps
  its words and answer. A finished hearing does not change. A change is made only to a served version.
- The daily reminder read is the worker's alone. Slice G's tables are case tables and join the purge order.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

from app.core.contracts.case_tables import CASE_TABLES, PURGE_ORDER
from tests.support.contracts import add_report_version
from tests.support.seed import Seed

Refused = psycopg.errors.InsufficientPrivilege
G_TABLES = (
    "court_directions",
    "hearings",
    "deadlines",
    "deadline_changes",
    "written_question_rounds",
    "written_questions",
    "opposing_reports",
    "opposing_claims",
    "report_changes",
    "may_matter",
)


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "viewer", "outsider")}
    p["org"] = seed.org(
        kind="team", members={p["expert"]: "expert", p["assistant"]: "assistant", p["viewer"]: "expert"}
    )
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(
        p["org"], p["expert"], status="served", members={p["assistant"]: "assistant", p["viewer"]: "viewer"}
    )
    return p


@contextmanager
def as_person(api_dsn: str, person: uuid.UUID) -> Iterator[psycopg.Connection]:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        yield conn


def add_deadline(conn: psycopg.Connection, case: uuid.UUID, due: str = "2026-04-20") -> uuid.UUID:
    row = conn.execute(
        "insert into app.deadlines (case_id, kind, due_on, timezone, created_from, source)"
        " values (%s, 'zz_deadline', %s, 'UTC', 'manual', 'manual') returning id",
        (case, due),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def test_slice_g_tables_are_case_tables_in_the_purge_order(admin: psycopg.Connection) -> None:
    for table in G_TABLES:
        assert table in CASE_TABLES and table in PURGE_ORDER
    row = admin.execute("select app.case_purge_order()").fetchone()
    assert row is not None and list(row[0]) == list(PURGE_ORDER)


def test_the_case_sees_deadlines_and_only_setup_roles_add_them(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["assistant"]) as conn:
        deadline = add_deadline(conn, team["case"])
    with as_person(api_dsn, team["viewer"]) as conn:
        assert conn.execute("select id from app.deadlines where id = %s", (deadline,)).fetchone() is not None
    with as_person(api_dsn, team["outsider"]) as conn:
        assert conn.execute("select id from app.deadlines where case_id = %s", (team["case"],)).fetchall() == []
    with pytest.raises(Refused), as_person(api_dsn, team["viewer"]) as conn:
        add_deadline(conn, team["case"])


def test_a_deadline_moves_only_with_its_change_recorded(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["expert"]) as conn:
        deadline = add_deadline(conn, team["case"])
    with pytest.raises(psycopg.DatabaseError) as refused, as_person(api_dsn, team["expert"]) as conn:
        conn.execute("update app.deadlines set due_on = '2026-05-01' where id = %s", (deadline,))
    assert refused.value.sqlstate == "AV409"
    with as_person(api_dsn, team["expert"]) as conn:
        conn.execute(
            "insert into app.deadline_changes (case_id, deadline_id, old_due_on, new_due_on, reason, source)"
            " values (%s, %s, '2026-04-20', '2026-05-01', 'direction', 'direction')",
            (team["case"], deadline),
        )
        conn.execute("update app.deadlines set due_on = '2026-05-01' where id = %s", (deadline,))
        row = conn.execute("select due_on::text, version from app.deadlines where id = %s", (deadline,)).fetchone()
    assert row == ("2026-05-01", 2)


def test_the_api_cannot_write_questions_or_may_matter_rows(
    api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    rnd = admin.execute(
        "insert into app.written_question_rounds (case_id, number, received_on)"
        " values (%s, 1, '2026-03-16') returning id",
        (team["case"],),
    ).fetchone()
    assert rnd is not None
    with pytest.raises(Refused), as_person(api_dsn, team["expert"]) as conn:
        conn.execute(
            "insert into app.written_questions (case_id, round_id, number, text_verbatim) values (%s, %s, 1, '\\x00')",
            (team["case"], rnd[0]),
        )
    page = seed.page(team["case"], seed.document(team["case"]), 1)
    with pytest.raises(Refused), as_person(api_dsn, team["expert"]) as conn:
        conn.execute(
            "insert into app.may_matter (case_id, page_id, fact_id, question_id, why) values (%s, %s, %s, %s, 'x')",
            (team["case"], page, uuid.uuid4(), uuid.uuid4()),
        )


def test_an_answered_question_keeps_its_answer(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    rnd = admin.execute(
        "insert into app.written_question_rounds (case_id, number, received_on)"
        " values (%s, 1, '2026-03-16') returning id",
        (team["case"],),
    ).fetchone()
    assert rnd is not None
    q = admin.execute(
        "insert into app.written_questions (case_id, round_id, number, text_verbatim, answer_text)"
        " values (%s, %s, 1, '\\x01', '\\x02') returning id",
        (team["case"], rnd[0]),
    ).fetchone()
    assert q is not None
    admin.execute("update app.written_questions set status = 'answered' where id = %s", (q[0],))
    with pytest.raises(psycopg.DatabaseError, match="does not change"):
        admin.execute("update app.written_questions set answer_text = '\\x03' where id = %s", (q[0],))
    admin.execute("update app.written_questions set status = 'served' where id = %s", (q[0],))


def test_a_finished_hearing_does_not_change(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    h = admin.execute(
        "insert into app.hearings (case_id, type, starts_on, ends_on)"
        " values (%s, 'zz_hearing', '2026-09-01', '2026-09-02')"
        " returning id",
        (team["case"],),
    ).fetchone()
    assert h is not None
    admin.execute("update app.hearings set status = 'heard' where id = %s", (h[0],))
    with pytest.raises(psycopg.DatabaseError, match="does not change"):
        admin.execute("update app.hearings set starts_on = '2026-09-03', ends_on = '2026-09-04' where id = %s", (h[0],))


def test_a_change_is_made_only_to_a_served_version(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    draft = add_report_version(admin, team["case"])
    para = admin.execute(
        "insert into app.report_paragraphs (case_id, report_version_id, para_no, section, content_control_id, text)"
        " values (%s, %s, 1, 'opinion', 'para-1', '\\x00') returning id",
        (team["case"], draft),
    ).fetchone()
    assert para is not None
    admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("a" * 64, draft))
    with pytest.raises(psycopg.DatabaseError, match="served version"):
        admin.execute(
            "insert into app.report_changes"
            " (case_id, served_version_id, kind, paragraph_id, old_text, new_text, reason)"
            " values (%s, %s, 'correction', %s, '\\x00', '\\x01', '\\x02')",
            (team["case"], draft, para[0]),
        )


def test_the_reminder_read_is_the_workers_alone(api_dsn: str, worker_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with pytest.raises(Refused), as_person(api_dsn, team["expert"]) as conn:
        conn.execute("select * from app.after_serving_due_deadlines(current_date, 3)")
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select * from app.after_serving_due_deadlines(current_date, 3)").fetchall()
