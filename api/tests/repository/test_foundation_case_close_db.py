"""Closing a case (state-machines.md "Case"; slice H builds the screens on these).

- served → settled needs `case.settle`, settled → archived `case.archive`, settled or archived →
  active `case.reopen`: the named expert only, no step-up. Helpers and organisation admins cannot.
- `cases.settled_at` is set by the database when the case is settled, kept when it is archived and
  cleared when it is reopened (the retention clock restarts from the next settlement).
- `case.view_audit` and `case.view_certificate`: the named expert and organisation owners/admins.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.auth.permissions import CASE_ACTIONS
from tests.support.seed import Seed

Refused = psycopg.errors.InsufficientPrivilege


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "admin", "owner")}
    p["org"] = seed.org(
        kind="team",
        members={p["expert"]: "expert", p["assistant"]: "assistant", p["admin"]: "admin", p["owner"]: "owner"},
    )
    return p


def case_for(seed: Seed, team: dict[str, uuid.UUID], status: str) -> uuid.UUID:
    return seed.case(team["org"], team["expert"], status=status, members={team["assistant"]: "assistant"})


def move_as(api_dsn: str, person: uuid.UUID, case: uuid.UUID, status: str) -> int:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        return conn.execute("update app.cases set status = %s where id = %s", (status, case)).rowcount


def settled_at(admin: psycopg.Connection, case: uuid.UUID) -> object:
    row = admin.execute("select settled_at from app.cases where id = %s", (case,)).fetchone()
    assert row is not None
    return row[0]


def test_the_close_actions_are_the_named_experts_without_step_up() -> None:
    for action in ("case.settle", "case.archive", "case.reopen"):
        rule = CASE_ACTIONS[action]
        assert rule.case_roles == frozenset({"named_expert"}), action
        assert rule.org == "none" and not rule.step_up, action


def test_the_audit_and_certificate_are_for_the_named_expert_and_organisation_admins() -> None:
    for action in ("case.view_audit", "case.view_certificate"):
        rule = CASE_ACTIONS[action]
        assert rule.case_roles == frozenset({"named_expert"}), action
        assert rule.org == "admin" and not rule.step_up, action


@pytest.mark.parametrize(
    ("start", "target"), [("served", "settled"), ("settled", "archived"), ("settled", "active"), ("archived", "active")]
)
def test_the_named_expert_settles_archives_and_reopens(
    start: str, target: str, api_dsn: str, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, start)
    assert move_as(api_dsn, team["expert"], case, target) == 1
    assert admin.execute("select status from app.cases where id = %s", (case,)).fetchone() == (target,)


@pytest.mark.parametrize("person", ["assistant", "admin", "owner"])
@pytest.mark.parametrize(
    ("start", "target"), [("served", "settled"), ("settled", "archived"), ("settled", "active"), ("archived", "active")]
)
def test_nobody_else_settles_archives_or_reopens(
    person: str,
    start: str,
    target: str,
    api_dsn: str,
    seed: Seed,
    admin: psycopg.Connection,
    team: dict[str, uuid.UUID],
) -> None:
    case = case_for(seed, team, start)
    with pytest.raises(Refused):
        move_as(api_dsn, team[person], case, target)
    assert admin.execute("select status from app.cases where id = %s", (case,)).fetchone() == (start,)


def test_settled_at_is_set_on_settling_kept_on_archiving_and_cleared_on_reopening(
    seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, "served")
    assert settled_at(admin, case) is None
    admin.execute("update app.cases set status = 'settled' where id = %s", (case,))
    first = settled_at(admin, case)
    assert first is not None
    admin.execute("update app.cases set status = 'archived' where id = %s", (case,))
    assert settled_at(admin, case) == first
    admin.execute("update app.cases set status = 'active' where id = %s", (case,))
    assert settled_at(admin, case) is None


def test_settled_at_cannot_be_written_by_hand(
    seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, "served")
    admin.execute("update app.cases set settled_at = '2020-01-01' where id = %s", (case,))
    assert settled_at(admin, case) is None
    admin.execute("update app.cases set status = 'settled' where id = %s", (case,))
    first = settled_at(admin, case)
    admin.execute("update app.cases set settled_at = '2020-01-01', label = 'Case Y' where id = %s", (case,))
    assert settled_at(admin, case) == first


def test_the_retention_policy_is_a_short_code(
    seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, "active")
    for policy in ("delete_now", "keep_default", "return_then_delete", None):
        admin.execute("update app.cases set retention_policy = %s where id = %s", (policy, case))
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("update app.cases set retention_policy = 'Keep it for ever!' where id = %s", (case,))


def test_only_a_deleted_case_may_lose_its_label(
    seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = case_for(seed, team, "active")
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute("update app.cases set label = null where id = %s", (case,))
