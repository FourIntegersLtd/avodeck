"""Slice H's tables (migration 0800): `legal_holds` and `deletion_certificates`.

- A legal hold pauses deletion. The named expert or an organisation owner sets and releases one
  (permissions.md `legal_hold.set`, `legal_hold.release`); its reason is a short code, never free
  text. Only `released_at` and `released_by` change after it is set. Once deletion of the case has
  started (its certificate row exists) no new hold can be set: the run would be half done.
- A deletion certificate is written by the case's own deletion job only and read by the named
  expert and organisation owners/admins (`case.view_certificate`). It is kept after the case's
  rows are purged (`KEPT_AFTER_CASE_DELETION`), with counts, hashes and dates only (rule 6).
- `app.close_case_audit(case)` gives the case's audit rows to whoever may `case.view_audit`.
- `app.close_certificate_recipients(case)` gives the deletion job its own case's named expert's address.
- `app.close_auto_archive(run_date, days)` and `app.close_release_expired_holds(run_date)` are the
  daily sweep's, for the worker only.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date, timedelta

import psycopg
import pytest

from app.core.contracts.case_tables import CASE_TABLES, KEPT_AFTER_CASE_DELETION
from tests.support.seed import Seed

Refused = psycopg.errors.InsufficientPrivilege


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "admin", "owner", "outsider")}
    p["org"] = seed.org(
        kind="team",
        members={p["expert"]: "expert", p["assistant"]: "assistant", p["admin"]: "admin", p["owner"]: "owner"},
    )
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(p["org"], p["expert"], status="served", members={p["assistant"]: "assistant"})
    return p


@contextmanager
def as_person(api_dsn: str, person: uuid.UUID) -> Iterator[psycopg.Connection]:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        yield conn


@contextmanager
def as_job(worker_dsn: str, case: uuid.UUID | None) -> Iterator[psycopg.Connection]:
    with psycopg.connect(worker_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.job_case_id', %s, true)", (str(case) if case else "",))
        yield conn


def add_hold(conn: psycopg.Connection, case: uuid.UUID, person: uuid.UUID, reason: str = "appeal") -> uuid.UUID:
    row = conn.execute(
        "insert into app.legal_holds (case_id, reason, set_by) values (%s, %s, %s) returning id", (case, reason, person)
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def add_certificate(conn: psycopg.Connection, case: uuid.UUID) -> uuid.UUID:
    row = conn.execute(
        "insert into app.deletion_certificates (case_id, counts, hashes, log_chain_heads)"
        " values (%s, '{}', '{}', '{}') returning id",
        (case,),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


# --- registry -------------------------------------------------------------------------------------


def test_legal_holds_are_case_rows_and_certificates_outlive_the_case() -> None:
    assert "legal_holds" in CASE_TABLES
    assert "deletion_certificates" in KEPT_AFTER_CASE_DELETION


def test_a_table_holding_case_rows_that_is_not_registered_is_found(admin: psycopg.Connection) -> None:
    """The registry check (tests/repository/test_foundation_contract_tables.py) sees any new table with a case_id."""
    with admin.transaction():
        admin.execute("create table app.zz_unregistered (case_id uuid)")
        rows = admin.execute(
            "select table_name from information_schema.columns where table_schema = 'app' and column_name = 'case_id'"
        ).fetchall()
        missing = {r[0] for r in rows} - CASE_TABLES - KEPT_AFTER_CASE_DELETION
        assert missing == {"zz_unregistered"}
        admin.execute("drop table app.zz_unregistered")


# --- legal holds ----------------------------------------------------------------------------------


@pytest.mark.parametrize("person", ["expert", "owner"])
def test_the_named_expert_or_an_owner_sets_and_releases_a_hold(
    person: str, api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    until = date.today() + timedelta(days=30)
    with as_person(api_dsn, team[person]) as conn:
        row = conn.execute(
            "insert into app.legal_holds (case_id, reason, set_by, until) values (%s, 'related_claim', %s, %s)"
            " returning id",
            (team["case"], team[person], until),
        ).fetchone()
    assert row is not None
    with as_person(api_dsn, team[person]) as conn:
        changed = conn.execute(
            "update app.legal_holds set released_at = now(), released_by = %s where id = %s", (team[person], row[0])
        ).rowcount
    assert changed == 1
    found = admin.execute("select released_by, until from app.legal_holds where id = %s", (row[0],)).fetchone()
    assert found == (team[person], until)


@pytest.mark.parametrize("person", ["assistant", "admin"])
def test_nobody_else_sets_a_hold(person: str, api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with pytest.raises(Refused), as_person(api_dsn, team[person]) as conn:
        add_hold(conn, team["case"], team[person])


def test_case_members_see_holds_and_outsiders_do_not(
    api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    add_hold(admin, team["case"], team["expert"])
    for person, seen in (("assistant", 1), ("admin", 1), ("outsider", 0)):
        with as_person(api_dsn, team[person]) as conn:
            row = conn.execute("select count(*) from app.legal_holds where case_id = %s", (team["case"],)).fetchone()
        assert row == (seen,), person


def test_a_hold_reason_is_a_short_code(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        add_hold(admin, team["case"], team["expert"], reason="Appeal lodged by Mr Example")


def test_only_the_release_changes_after_a_hold_is_set(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["expert"]) as conn:
        hold = add_hold(conn, team["case"], team["expert"])
    for change in ("reason = 'other'", "until = current_date", "set_by = released_by"):
        with pytest.raises(Refused), as_person(api_dsn, team["expert"]) as conn:
            conn.execute(f"update app.legal_holds set {change} where id = %s", (hold,))  # noqa: S608
    with pytest.raises(Refused), as_person(api_dsn, team["expert"]) as conn:
        conn.execute("delete from app.legal_holds where id = %s", (hold,))


def test_no_hold_once_deletion_has_started(
    worker_dsn: str, api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    with as_job(worker_dsn, team["case"]) as conn:
        add_certificate(conn, team["case"])
    with pytest.raises(psycopg.Error) as refused, as_person(api_dsn, team["expert"]) as conn:
        add_hold(conn, team["case"], team["expert"])
    assert refused.value.sqlstate == "AV409"


def test_the_deletion_job_sees_its_own_cases_holds(
    worker_dsn: str, admin: psycopg.Connection, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    add_hold(admin, team["case"], team["expert"])
    other = seed.case(team["org"], team["expert"])
    add_hold(admin, other, team["expert"])
    with as_job(worker_dsn, team["case"]) as conn:
        rows = conn.execute("select case_id from app.legal_holds").fetchall()
    assert rows == [(team["case"],)]


# --- deletion certificates --------------------------------------------------------------------------


def test_only_the_cases_own_job_writes_its_certificate(
    worker_dsn: str, api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    other = seed.case(team["org"], team["expert"])
    with pytest.raises(Refused), as_job(worker_dsn, other) as conn:
        add_certificate(conn, team["case"])
    with pytest.raises(Refused), as_person(api_dsn, team["expert"]) as conn:
        add_certificate(conn, team["case"])
    with as_job(worker_dsn, team["case"]) as conn:
        cert = add_certificate(conn, team["case"])
        changed = conn.execute(
            "update app.deletion_certificates set deleted_at = now(), counts = '{\"pages\": 2}' where id = %s", (cert,)
        ).rowcount
    assert changed == 1


def test_a_case_has_one_certificate(worker_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_job(worker_dsn, team["case"]) as conn:
        add_certificate(conn, team["case"])
    with pytest.raises(psycopg.errors.UniqueViolation), as_job(worker_dsn, team["case"]) as conn:
        add_certificate(conn, team["case"])


def test_the_named_expert_and_organisation_admins_read_the_certificate(
    worker_dsn: str, api_dsn: str, team: dict[str, uuid.UUID]
) -> None:
    with as_job(worker_dsn, team["case"]) as conn:
        add_certificate(conn, team["case"])
    for person, seen in (("expert", 1), ("admin", 1), ("owner", 1), ("assistant", 0), ("outsider", 0)):
        with as_person(api_dsn, team[person]) as conn:
            row = conn.execute(
                "select count(*) from app.deletion_certificates where case_id = %s", (team["case"],)
            ).fetchone()
        assert row == (seen,), person


def test_the_certificate_and_ledger_outlive_the_purge(
    worker_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    with as_job(worker_dsn, team["case"]) as conn:
        add_certificate(conn, team["case"])
        conn.execute("select app.purge_case(%s)", (team["case"],))
    assert admin.execute(
        "select count(*) from app.deletion_certificates where case_id = %s", (team["case"],)
    ).fetchone() == (1,)
    assert admin.execute("select count(*) from app.deletion_ledger where case_id = %s", (team["case"],)).fetchone() == (
        1,
    )


def test_a_purge_removes_released_holds(worker_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    hold = add_hold(admin, team["case"], team["expert"])
    admin.execute("update app.legal_holds set released_at = now(), released_by = set_by where id = %s", (hold,))
    with as_job(worker_dsn, team["case"]) as conn:
        counts = conn.execute("select app.purge_case(%s)", (team["case"],)).fetchone()
    assert counts is not None and counts[0]["legal_holds"] == 1


# --- functions --------------------------------------------------------------------------------------


def test_the_audit_trail_is_for_the_named_expert_and_organisation_admins(
    api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    admin.execute(
        "insert into app.audit_log (case_id, person_id, action, target) values (%s, %s, 'zz.thing', 'case:x')",
        (team["case"], team["assistant"]),
    )
    for person in ("expert", "admin"):
        with as_person(api_dsn, team[person]) as conn:
            rows = conn.execute("select action, person_id from app.close_case_audit(%s)", (team["case"],)).fetchall()
        assert ("zz.thing", team["assistant"]) in rows, person
    for person in ("assistant", "outsider"):
        with pytest.raises(Refused), as_person(api_dsn, team[person]) as conn:
            conn.execute("select * from app.close_case_audit(%s)", (team["case"],))


def test_the_deletion_job_finds_its_own_cases_named_expert(
    worker_dsn: str, admin: psycopg.Connection, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    email = admin.execute("select email from app.people where id = %s", (team["expert"],)).fetchone()
    with as_job(worker_dsn, team["case"]) as conn:
        rows = conn.execute("select role, email from app.close_certificate_recipients(%s)", (team["case"],)).fetchall()
    assert email is not None and rows == [("named_expert", email[0])]
    other = seed.case(team["org"], team["expert"])
    with pytest.raises(Refused), as_job(worker_dsn, other) as conn:
        conn.execute("select * from app.close_certificate_recipients(%s)", (team["case"],))


def test_settled_cases_move_to_the_archive_after_the_given_days(
    worker_dsn: str, admin: psycopg.Connection, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    old = seed.case(team["org"], team["expert"], status="settled")
    recent = seed.case(team["org"], team["expert"], status="settled")
    # Back-date the settlement (the database stamps settled_at itself, so its triggers are bypassed here).
    with admin.transaction():
        admin.execute("set local session_replication_role = replica")
        admin.execute("update app.cases set settled_at = now() - interval '31 days' where id = %s", (old,))
    with as_job(worker_dsn, None) as conn:
        row = conn.execute("select app.close_auto_archive(%s, 30)", (date.today(),)).fetchone()
    assert row is not None and row[0] >= 1
    rows = admin.execute("select id, status from app.cases where id = any(%s)", ([old, recent],)).fetchall()
    statuses: dict[uuid.UUID, str] = dict(rows)
    assert statuses == {old: "archived", recent: "settled"}
    audit = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.archived'", (old,)
    ).fetchone()
    assert audit == (1,)


def test_holds_past_their_end_date_are_released(
    worker_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    ended = add_hold(admin, team["case"], team["expert"])
    lasting = add_hold(admin, team["case"], team["expert"])
    admin.execute("update app.legal_holds set until = current_date - 1 where id = %s", (ended,))
    with as_job(worker_dsn, None) as conn:
        conn.execute("select app.close_release_expired_holds(%s)", (date.today(),))
    released: dict[uuid.UUID, object] = dict(
        admin.execute(
            "select id, released_at is not null from app.legal_holds where id = any(%s)", ([ended, lasting],)
        ).fetchall()
    )
    assert released == {ended: True, lasting: False}


@pytest.mark.parametrize(
    "fn", ["app.close_auto_archive(current_date, 30)", "app.close_release_expired_holds(current_date)"]
)
def test_the_daily_functions_are_the_workers(fn: str, api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with pytest.raises(Refused), as_person(api_dsn, team["expert"]) as conn:
        conn.execute(f"select {fn}")  # noqa: S608


# --- after review: releases, finished certificates, withdrawal ------------------------------------------


def refused(api_dsn: str, person: uuid.UUID, sql: str, params: tuple[object, ...]) -> str | None:
    with pytest.raises(psycopg.Error) as err, as_person(api_dsn, person) as conn:
        conn.execute(sql, params)
    return err.value.sqlstate


RELEASE = "update app.legal_holds set released_at = now(), released_by = %s where id = %s"
UNRELEASE = "update app.legal_holds set released_at = null, released_by = null where id = %s"


def test_a_released_hold_is_never_unreleased_or_released_again(
    api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    hold = add_hold(admin, team["case"], team["expert"])
    with as_person(api_dsn, team["expert"]) as conn:
        conn.execute(RELEASE, (team["expert"], hold))
    assert refused(api_dsn, team["expert"], UNRELEASE, (hold,)) == "AV409"
    assert refused(api_dsn, team["owner"], RELEASE, (team["owner"], hold)) == "AV409"
    row = admin.execute("select released_by from app.legal_holds where id = %s", (hold,)).fetchone()
    assert row == (team["expert"],)


def test_a_release_is_by_the_person_releasing_it_now(
    api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    hold = add_hold(admin, team["case"], team["expert"])
    assert refused(api_dsn, team["expert"], RELEASE, (team["owner"], hold)) == "AV409"
    backdated = "update app.legal_holds set released_at = now() - interval '1 day', released_by = %s where id = %s"
    assert refused(api_dsn, team["expert"], backdated, (team["expert"], hold)) == "AV409"
    assert refused(api_dsn, team["expert"], UNRELEASE, (hold,)) == "AV409"


def test_a_finished_certificate_never_changes(worker_dsn: str, team: dict[str, uuid.UUID]) -> None:
    case = team["case"]
    with as_job(worker_dsn, case) as conn:
        conn.execute("insert into app.deletion_certificates (case_id) values (%s)", (case,))
    with as_job(worker_dsn, case) as conn:  # still running: it may be completed
        conn.execute("update app.deletion_certificates set sent_to = '{named_expert}' where case_id = %s", (case,))
        conn.execute("update app.deletion_certificates set deleted_at = now() where case_id = %s", (case,))
    for change in ("counts = '{\"pages\": 1}'", "deleted_at = null", "sent_to = '{}'", "backup_expiry_on = null"):
        with pytest.raises(psycopg.Error) as err, as_job(worker_dsn, case) as conn:
            conn.execute(f"update app.deletion_certificates set {change} where case_id = %s", (case,))  # noqa: S608
        assert err.value.sqlstate == "AV409", change


def withdraw_on(admin: psycopg.Connection, case: uuid.UUID, day: date) -> None:
    with admin.transaction():
        admin.execute("set local session_replication_role = replica")
        admin.execute("update app.cases set status = 'withdrawn' where id = %s", (case,))
    admin.execute(
        "insert into app.audit_log (case_id, action, target, at)"
        " values (%s, 'case.withdrawn', %s, (%s::date + time '12:00')::timestamptz)",
        (case, f"case:{case}", day),
    )


def test_withdrawing_a_case_records_the_day_for_its_members(
    api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = team["case"]
    admin.execute("update app.cases set status = 'withdrawn' where id = %s", (case,))
    audit = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.withdrawn'", (case,)
    ).fetchone()
    assert audit == (1,)
    with as_person(api_dsn, team["assistant"]) as conn:
        assert conn.execute("select app.close_withdrawn_on(%s)", (case,)).fetchone() == (date.today(),)
    with pytest.raises(Refused), as_person(api_dsn, team["outsider"]) as conn:
        conn.execute("select app.close_withdrawn_on(%s)", (case,))


def test_withdrawn_cases_move_to_the_archive_after_the_given_days(
    worker_dsn: str, admin: psycopg.Connection, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    old = seed.case(team["org"], team["expert"], status="active")
    recent = seed.case(team["org"], team["expert"], status="active")
    withdraw_on(admin, old, date.today() - timedelta(days=31))
    withdraw_on(admin, recent, date.today() - timedelta(days=5))
    with as_job(worker_dsn, None) as conn:
        conn.execute("select app.close_auto_archive(%s, 30)", (date.today(),))
    rows = admin.execute("select id, status from app.cases where id = any(%s)", ([old, recent],)).fetchall()
    statuses: dict[uuid.UUID, str] = dict(rows)
    assert statuses == {old: "archived", recent: "withdrawn"}


def test_deletion_started_is_seen_by_every_case_member_and_no_outsider(
    worker_dsn: str, api_dsn: str, team: dict[str, uuid.UUID]
) -> None:
    case = team["case"]
    with as_person(api_dsn, team["assistant"]) as conn:
        assert conn.execute("select app.close_deletion_started(%s)", (case,)).fetchone() == (False,)
    with as_job(worker_dsn, case) as conn:
        conn.execute("insert into app.deletion_certificates (case_id) values (%s)", (case,))
    with as_person(api_dsn, team["assistant"]) as conn:
        assert conn.execute("select app.close_deletion_started(%s)", (case,)).fetchone() == (True,)
    with pytest.raises(Refused), as_person(api_dsn, team["outsider"]) as conn:
        conn.execute("select app.close_deletion_started(%s)", (case,))
