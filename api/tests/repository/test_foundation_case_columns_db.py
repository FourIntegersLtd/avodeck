"""Rule 9 in the database: each sensitive column of a case needs its own action (docs/spec/permissions.md).

Row-level security lets `case.edit_setup` or `case.manage_members` update a case; the database then
checks each change against the action it is, for the person the restricted role acts for:
- `named_expert_id`: `case.reassign_named_expert` (organisation owner/admin), before serving only;
- `retention_policy`, `delete_after`, `on_hold`: `case.retention`;
- status → `deleted`: `case.delete_now` (unserved only); → `withdrawn`: `case.withdraw`;
  → `served`: `report.serve`; any other move: `case.edit_setup`;
- every other column: `case.edit_setup`.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.contracts import add_report_version
from tests.support.seed import Seed

Refused = psycopg.errors.InsufficientPrivilege


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "admin", "owner", "other_expert")}
    org = seed.org(
        kind="team",
        members={
            p["expert"]: "expert",
            p["assistant"]: "assistant",
            p["admin"]: "admin",
            p["owner"]: "owner",
            p["other_expert"]: "expert",
        },
    )
    p["org"] = org
    return p


def case_for(seed: Seed, team: dict[str, uuid.UUID], status: str = "active") -> uuid.UUID:
    return seed.case(team["org"], team["expert"], status=status, members={team["assistant"]: "assistant"})


def update_as(api_dsn: str, person: uuid.UUID, case: uuid.UUID, assignment: str, *params: object) -> int:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        cur = conn.execute(f"update app.cases set {assignment} where id = %s", (*params, case))  # type: ignore[arg-type]
        return cur.rowcount


@pytest.mark.parametrize(
    ("assignment", "params"),
    [
        ("named_expert_id = %s", "other_expert"),
        ("status = 'deleted'", None),
        ("status = 'withdrawn'", None),
        ("status = 'served'", None),
        ("retention_policy = 'zz_policy'", None),
        ("delete_after = '2031-01-01'", None),
        ("on_hold = true", None),
    ],
)
def test_an_assistant_cannot_change_the_sensitive_columns(
    assignment: str, params: str | None, api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team)
    args = (team[params],) if params else ()
    with pytest.raises(Refused):
        update_as(api_dsn, team["assistant"], case, assignment, *args)
    row = admin.execute("select named_expert_id, status, on_hold from app.cases where id = %s", (case,)).fetchone()
    assert row == (team["expert"], "active", False)


def test_an_assistant_can_still_edit_the_setup(api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]) -> None:
    assert update_as(api_dsn, team["assistant"], case_for(seed, team), "label = 'Case Y'") == 1


def test_an_organisation_admin_cannot_edit_the_setup(api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]) -> None:
    with pytest.raises(Refused):
        update_as(api_dsn, team["admin"], case_for(seed, team), "label = 'Case Y'")


def test_an_organisation_admin_can_reassign_the_named_expert_before_serving(
    api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team)
    assert update_as(api_dsn, team["admin"], case, "named_expert_id = %s", team["other_expert"]) == 1
    assert admin.execute("select named_expert_id from app.cases where id = %s", (case,)).fetchone() == (
        team["other_expert"],
    )


@pytest.mark.parametrize("served", ["case_status", "report_version"])
def test_nobody_reassigns_the_named_expert_after_serving(
    served: str, api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, status="served" if served == "case_status" else "active")
    if served == "report_version":
        add_report_version(admin, case, status="served")
    with pytest.raises(Refused):
        update_as(api_dsn, team["admin"], case, "named_expert_id = %s", team["other_expert"])


def test_the_named_expert_cannot_reassign_themselves(api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]) -> None:
    with pytest.raises(Refused):
        update_as(api_dsn, team["expert"], case_for(seed, team), "named_expert_id = %s", team["other_expert"])


@pytest.mark.parametrize("person", ["expert", "admin"])
def test_retention_is_for_the_named_expert_or_an_organisation_admin(
    person: str, api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team)
    assert update_as(api_dsn, team[person], case, "retention_policy = 'zz_policy', delete_after = '2031-01-01'") == 1
    assert update_as(api_dsn, team[person], case, "on_hold = true") == 1


@pytest.mark.parametrize("person", ["expert", "owner"])
def test_the_named_expert_or_the_organisation_owner_can_delete_an_unserved_case(
    person: str, api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, status="draft")
    assert update_as(api_dsn, team[person], case, "status = 'deleted'") == 1
    assert admin.execute("select status from app.cases where id = %s", (case,)).fetchone() == ("deleted",)


def test_an_organisation_admin_who_is_not_the_owner_cannot_delete_now(
    api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(Refused):
        update_as(api_dsn, team["admin"], case_for(seed, team, status="draft"), "status = 'deleted'")


@pytest.mark.parametrize("served", ["case_status", "report_version"])
def test_nobody_deletes_a_served_case_now(
    served: str, api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, status="served" if served == "case_status" else "active")
    if served == "report_version":
        add_report_version(admin, case, status="served")
    for person in ("expert", "owner"):
        with pytest.raises(Refused):
            update_as(api_dsn, team[person], case, "status = 'deleted'")


def test_the_named_expert_withdraws_and_serves(api_dsn: str, seed: Seed, team: dict[str, uuid.UUID]) -> None:
    assert update_as(api_dsn, team["expert"], case_for(seed, team), "status = 'served'") == 1
    assert update_as(api_dsn, team["expert"], case_for(seed, team), "status = 'withdrawn'") == 1
