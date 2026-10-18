"""`cases.closed_at`: when the case closed (architecture 4.10; state-machines.md "Case").

Set by the database when a case is settled or withdrawn, kept when it is archived or deleted, and
cleared when it is reopened (the next settlement sets it again). Nobody writes it by hand.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.seed import Seed


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return {"expert": expert, "org": org}


def closed_at(admin: psycopg.Connection, case: uuid.UUID) -> object:
    row = admin.execute("select closed_at from app.cases where id = %s", (case,)).fetchone()
    assert row is not None
    return row[0]


def set_status(admin: psycopg.Connection, case: uuid.UUID, status: str) -> None:
    admin.execute("update app.cases set status = %s where id = %s", (status, case))


def test_a_new_or_open_case_has_not_closed(seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    assert closed_at(admin, seed.case(team["org"], team["expert"], status="served")) is None


def test_settling_sets_it_archiving_keeps_it_and_reopening_clears_it(
    seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = seed.case(team["org"], team["expert"], status="served")
    set_status(admin, case, "settled")
    settled = admin.execute("select closed_at, settled_at from app.cases where id = %s", (case,)).fetchone()
    assert settled is not None and settled[0] is not None and settled[0] == settled[1]
    set_status(admin, case, "archived")
    assert closed_at(admin, case) == settled[0]
    set_status(admin, case, "active")
    assert closed_at(admin, case) is None


def test_withdrawing_sets_it_and_archiving_keeps_it(
    seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = seed.case(team["org"], team["expert"], status="active")
    set_status(admin, case, "withdrawn")
    withdrawn = closed_at(admin, case)
    assert withdrawn is not None
    set_status(admin, case, "archived")
    assert closed_at(admin, case) == withdrawn


def test_nobody_writes_it_by_hand(
    api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = seed.case(team["org"], team["expert"], status="active")
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(team["expert"]),))
        conn.execute("update app.cases set closed_at = '2000-01-01' where id = %s", (case,))
    assert closed_at(admin, case) is None
