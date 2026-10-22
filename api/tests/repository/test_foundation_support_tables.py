"""Migration 0030: `support_staff` and `support_access_grants` (rule 9, S17, F38).

- `support_staff` is written by hand in SQL by an operator (docs/ops/support-staff.md); the running
  app (app_api, app_worker) never writes it. A person reads only their own row.
- `support_access_grants` is kept after the case is deleted (`KEPT_AFTER_CASE_DELETION`), as the
  record of the named expert's consent. The named expert gives a grant
  (`support_access.grant`) for one case, at most 24 hours; the reason is a short code. Anyone who
  may see the case's metadata sees its grants (shown on the case). Only `revoked_at` changes after
  a grant is given, and only by the named expert.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

from app.core.contracts.case_tables import CASE_TABLES, KEPT_AFTER_CASE_DELETION
from tests.support.seed import Seed

Refused = psycopg.errors.InsufficientPrivilege
Invalid = psycopg.errors.CheckViolation


@contextmanager
def as_person(api_dsn: str, person: uuid.UUID) -> Iterator[psycopg.Connection]:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        yield conn


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "owner", "staff", "outsider")}
    p["org"] = seed.org(kind="team", members={p["expert"]: "expert", p["assistant"]: "assistant", p["owner"]: "owner"})
    p["case"] = seed.case(p["org"], p["expert"], members={p["assistant"]: "assistant"})
    seed.support_staff(p["staff"])
    return p


def grant(conn: psycopg.Connection, case: uuid.UUID, by: uuid.UUID, hours: int = 24) -> uuid.UUID:
    row = conn.execute(
        "insert into app.support_access_grants (case_id, granted_by, granted_to, reason, expires_at)"
        " values (%s, %s, null, 'support_request', now() + make_interval(hours => %s)) returning id",
        (case, by, hours),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def test_grants_are_kept_as_the_record_of_consent_after_the_case_is_deleted() -> None:
    """Like the audit trail they hold ids and a short code only; on a deleted case they open nothing
    (a deleted case answers only its certificate, which support may not take)."""
    assert "support_access_grants" in KEPT_AFTER_CASE_DELETION
    assert "support_access_grants" not in CASE_TABLES


@pytest.mark.parametrize("role", ["app_api", "app_worker"])
def test_the_running_app_never_writes_support_staff(admin: psycopg.Connection, role: str) -> None:
    rows = admin.execute(
        "select privilege_type from information_schema.role_table_grants"
        " where table_schema = 'app' and table_name = 'support_staff' and grantee = %s",
        (role,),
    ).fetchall()
    assert {r[0] for r in rows} <= {"SELECT"}


def test_staff_see_only_their_own_row(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["staff"]) as conn:
        assert conn.execute("select person_id from app.support_staff").fetchall() == [(team["staff"],)]
    with as_person(api_dsn, team["outsider"]) as conn:
        assert conn.execute("select person_id from app.support_staff").fetchall() == []
    with as_person(api_dsn, team["staff"]) as conn, pytest.raises(Refused):
        conn.execute("insert into app.support_staff (person_id) values (%s)", (team["outsider"],))


def test_only_the_named_expert_gives_a_grant(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["expert"]) as conn:
        grant(conn, team["case"], team["expert"])
    for who in ("assistant", "owner", "staff"):
        with as_person(api_dsn, team[who]) as conn, pytest.raises(Refused):
            grant(conn, team["case"], team[who])
    with as_person(api_dsn, team["expert"]) as conn, pytest.raises(Refused):
        grant(conn, team["case"], team["assistant"])  # in someone else's name


def test_a_grant_lasts_at_most_24_hours(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["expert"]) as conn, pytest.raises(Invalid):
        grant(conn, team["case"], team["expert"], hours=25)


def test_a_grant_reason_is_a_short_code(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["expert"]) as conn, pytest.raises(Invalid):
        conn.execute(
            "insert into app.support_access_grants (case_id, granted_by, reason) values (%s, %s, %s)",
            (team["case"], team["expert"], "Patient said something"),
        )


def test_grants_are_shown_to_whoever_sees_the_case(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["expert"]) as conn:
        gid = grant(conn, team["case"], team["expert"])
    for who in ("expert", "assistant", "owner"):
        with as_person(api_dsn, team[who]) as conn:
            assert conn.execute("select id from app.support_access_grants").fetchall() == [(gid,)], who
    with as_person(api_dsn, team["outsider"]) as conn:
        assert conn.execute("select id from app.support_access_grants").fetchall() == []


def test_only_revoking_changes_a_grant(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with as_person(api_dsn, team["expert"]) as conn:
        gid = grant(conn, team["case"], team["expert"])
    with as_person(api_dsn, team["expert"]) as conn, pytest.raises(Refused):
        conn.execute(
            "update app.support_access_grants set expires_at = now() + interval '1 hour' where id = %s", (gid,)
        )
    with as_person(api_dsn, team["assistant"]) as conn:
        changed = conn.execute("update app.support_access_grants set revoked_at = now() where id = %s", (gid,)).rowcount
        assert changed == 0
    with as_person(api_dsn, team["expert"]) as conn:
        conn.execute("update app.support_access_grants set revoked_at = now() where id = %s", (gid,))
    with as_person(api_dsn, team["expert"]) as conn, pytest.raises(Refused):
        conn.execute("delete from app.support_access_grants where id = %s", (gid,))
