"""Rule 9 in the database: holding a case and changing its packs (docs/spec/permissions.md).

- `on_hold`: `case.hold` (named expert or assistant; an organisation admin not on the case cannot);
- `jurisdiction`, `case_type`, `specialty`, `pack_versions`: `case.change_jurisdiction` (named
  expert), and only while the case has never been served.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.contracts import add_report_version
from tests.support.seed import Seed

Refused = psycopg.errors.InsufficientPrivilege
PACK_CHANGES = [
    "jurisdiction = 'zz-other'",
    "case_type = 'zz-other'",
    "specialty = 'zz-other'",
    """pack_versions = '{"jurisdiction": "zz-test@9"}'::jsonb""",
]


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "admin")}
    p["org"] = seed.org(kind="team", members={p["expert"]: "expert", p["assistant"]: "assistant", p["admin"]: "admin"})
    return p


def case_for(seed: Seed, team: dict[str, uuid.UUID], status: str = "active") -> uuid.UUID:
    return seed.case(team["org"], team["expert"], status=status, members={team["assistant"]: "assistant"})


def update_as(api_dsn: str, person: uuid.UUID, case: uuid.UUID, assignment: str) -> int:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        return conn.execute(f"update app.cases set {assignment} where id = %s", (case,)).rowcount


@pytest.mark.parametrize("person", ["expert", "assistant"])
def test_the_named_expert_or_the_assistant_puts_a_case_on_hold_and_resumes_it(
    person: str, api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team)
    assert update_as(api_dsn, team[person], case, "on_hold = true") == 1
    assert admin.execute("select on_hold from app.cases where id = %s", (case,)).fetchone() == (True,)
    assert update_as(api_dsn, team[person], case, "on_hold = false") == 1


def test_an_organisation_admin_not_on_the_case_cannot_hold_it(
    api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(Refused):
        update_as(api_dsn, team["admin"], case_for(seed, team), "on_hold = true")


def test_holding_a_case_is_no_longer_a_retention_change(api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]) -> None:
    """The assistant may hold the case but still not change its retention."""
    case = case_for(seed, team)
    assert update_as(api_dsn, team["assistant"], case, "on_hold = true") == 1
    with pytest.raises(Refused):
        update_as(api_dsn, team["assistant"], case, "delete_after = '2031-01-01'")


@pytest.mark.parametrize("assignment", PACK_CHANGES)
def test_the_named_expert_changes_the_packs_before_serving(
    assignment: str, api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    assert update_as(api_dsn, team["expert"], case_for(seed, team), assignment) == 1


@pytest.mark.parametrize("assignment", PACK_CHANGES)
def test_the_assistant_cannot_change_the_packs(
    assignment: str, api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team)
    with pytest.raises(Refused):
        update_as(api_dsn, team["assistant"], case, assignment)
    assert admin.execute("select jurisdiction from app.cases where id = %s", (case,)).fetchone() == ("zz-test",)


@pytest.mark.parametrize("served", ["case_status", "report_version"])
def test_nobody_changes_the_packs_after_serving(
    served: str, api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, status="served" if served == "case_status" else "active")
    if served == "report_version":
        add_report_version(admin, case, status="served")
    with pytest.raises(Refused):
        update_as(api_dsn, team["expert"], case, "jurisdiction = 'zz-other'")
