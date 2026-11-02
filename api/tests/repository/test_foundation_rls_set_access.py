"""Row-level security checks access once per query, not once per row (architecture 9, ADR 0013).

`app.accessible_case_ids(action)` and `app.accessible_bundle_ids(action)` return the set of cases and
bundles a person may reach, and SELECT policies look each row up in that set. The sets must give exactly
the answers of `app.can_access_case` and `app.can_access_bundle` for every kind of person and every
action, or a policy would show a case to someone who may not see it (or hide one they may).
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta

import psycopg
import pytest

from app.core.auth.permissions import CASE_ACTIONS
from tests.support.seed import Seed

ACTIONS = sorted(CASE_ACTIONS)

SLOW_FORM = re.compile(
    r"^app\.can_access_(?:case\((?:case_id|id)|bundle\(bundle_id), '[a-z_.]+'::text\)$",
)


class World:
    """A matrix of people and cases covering every way `app.case_access` can answer."""

    people: dict[str, uuid.UUID | None]
    cases: dict[str, uuid.UUID]


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> World:
    w = World()
    p = seed.person
    expert, owner, admin_, assistant, viewer, ended_owner, org_expert = (p() for _ in range(7))
    co, sup, case_assistant, case_viewer, removed = (p() for _ in range(5))
    outsider, personal = p(), p()
    staff_any, staff_named, staff_expired, staff_none, staff_revoked, staff_removed = (p() for _ in range(6))
    closed_expert, closed_member, closed_owner = p(), p(), p()
    custodian, org_custodian, revoked_custodian, ex_custodian_owner = p(), p(), p(), p()
    other_expert = p()

    team = seed.org(
        kind="team",
        members={expert: "expert", owner: "owner", admin_: "admin", assistant: "assistant", viewer: "viewer",
                 org_expert: "expert", ended_owner: "owner", closed_owner: "owner"},
    )
    seed.end_membership(team, ended_owner)
    personal_org = seed.org(kind="personal", members={personal: "owner"})
    elsewhere = seed.org(
        kind="team",
        members={outsider: "owner", other_expert: "expert", org_custodian: "admin", ex_custodian_owner: "viewer"},
    )
    closing = seed.org(kind="team", members={closed_expert: "expert", closed_owner: "owner"})

    members = {co: "co_expert", sup: "supervisor", case_assistant: "assistant", case_viewer: "viewer",
               removed: "viewer", closed_member: "co_expert"}
    active = seed.case(team, expert, status="active", members=members)
    served = seed.case(team, expert, status="served", members=members)
    deleted = seed.case(team, expert, status="served", members=members)
    draft = seed.case(team, expert, status="draft")
    admin.execute("update app.case_members set removed_at = now() where person_id = %s", (removed,))
    mine = seed.case(personal_org, personal, status="active")
    theirs = seed.case(elsewhere, other_expert, status="served")
    closed_served = seed.case(closing, closed_expert, status="served", members={closed_member: "co_expert"})
    closed_active = seed.case(closing, closed_expert, status="active", members={closed_member: "co_expert"})
    seed.close_account(closed_expert)
    seed.close_account(closed_member)
    seed.close_account(closed_owner)

    for s in (staff_any, staff_named, staff_expired, staff_none, staff_revoked):
        seed.support_staff(s)
    seed.support_staff(staff_removed, removed=True)
    seed.support_grant(active, expert)
    seed.support_grant(served, expert, granted_to=staff_named)
    seed.support_grant(draft, expert, granted_to=staff_expired, expires_in=timedelta(hours=-1))
    seed.support_grant(draft, expert, granted_to=staff_revoked, revoked=True)
    seed.support_grant(deleted, expert)
    # a grant not given by the named expert counts for nothing
    seed.support_grant(theirs, expert)

    custody_staff = p()
    seed.support_staff(custody_staff)
    seed.records_custodian(expert, custodian)
    seed.records_custodian(other_expert, revoked_custodian)
    seed.custody_transfer(served, custody_staff, to_person=custodian)
    seed.custody_transfer(deleted, custody_staff, to_person=custodian)
    seed.case_status(deleted, "deleted")  # a tombstone keeps its transfer
    seed.custody_transfer(theirs, custody_staff, to_person=revoked_custodian, revoked=True)
    seed.custody_transfer(theirs, custody_staff, to_organisation=elsewhere)

    w.people = {
        "expert": expert, "owner": owner, "admin": admin_, "assistant": assistant, "viewer": viewer,
        "ended_owner": ended_owner, "org_expert": org_expert, "co": co, "sup": sup,
        "case_assistant": case_assistant, "case_viewer": case_viewer, "removed": removed,
        "outsider": outsider, "personal": personal, "other_expert": other_expert,
        "staff_any": staff_any, "staff_named": staff_named, "staff_expired": staff_expired,
        "staff_none": staff_none, "staff_revoked": staff_revoked, "staff_removed": staff_removed,
        "closed_expert": closed_expert, "closed_member": closed_member, "closed_owner": closed_owner,
        "custodian": custodian, "org_custodian": org_custodian, "revoked_custodian": revoked_custodian,
        "ex_custodian_owner": ex_custodian_owner, "custody_staff": custody_staff, "nobody": None,
    }
    w.cases = {
        "active": active, "served": served, "deleted": deleted, "draft": draft, "mine": mine,
        "theirs": theirs, "closed_served": closed_served, "closed_active": closed_active,
    }
    return w


def as_person(conn: psycopg.Connection, person: uuid.UUID | None) -> None:
    conn.execute("select set_config('app.person_id', %s, true)", (str(person) if person else "",))


def test_the_sets_answer_exactly_as_the_per_row_functions_do(
    world: World, admin: psycopg.Connection, api_dsn: str
) -> None:
    seeded = list(world.cases.values())
    bundles = {c: admin.execute("select bundle_id from app.cases where id = %s", (c,)).fetchone()[0] for c in seeded}  # type: ignore[index]
    wrong: list[str] = []
    allowed_somewhere = 0
    with psycopg.connect(api_dsn) as api:
        for name, person in world.people.items():
            for action in ACTIONS:
                with admin.transaction():
                    as_person(admin, person)
                    expected = {
                        c for c in seeded
                        if admin.execute("select app.can_access_case(%s, %s)", (c, action)).fetchone()[0]  # type: ignore[index]
                    }
                    expected_bundles = {
                        bundles[c] for c in seeded
                        if admin.execute("select app.can_access_bundle(%s, %s)", (bundles[c], action)).fetchone()[0]  # type: ignore[index]
                    }
                    got = {r[0] for r in admin.execute("select app.accessible_case_ids(%s)", (action,))} & set(seeded)
                    got_bundles = {
                        r[0] for r in admin.execute("select app.accessible_bundle_ids(%s)", (action,))
                    } & set(bundles.values())
                with api.transaction():
                    as_person(api, person)
                    api_got = {r[0] for r in api.execute("select app.accessible_case_ids(%s)", (action,))} & set(seeded)
                allowed_somewhere += len(expected)
                if got != expected or api_got != expected or got_bundles != expected_bundles:
                    wrong.append(f"{name}/{action}")
    assert not wrong, wrong
    assert allowed_somewhere > 200  # the matrix really exercises the allowed side


def test_an_unknown_action_gives_an_empty_set(world: World, admin: psycopg.Connection) -> None:
    with admin.transaction():
        as_person(admin, world.people["expert"])
        assert admin.execute("select count(*) from app.accessible_case_ids('no.such_action')").fetchone() == (0,)


def test_no_select_policy_checks_access_one_row_at_a_time(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select tablename, policyname, qual from pg_policies"
        " where schemaname = 'app' and cmd = 'SELECT' and 'app_api' = any (roles)"
    ).fetchall()
    slow = [f"{t}.{n}" for t, n, qual in rows if qual and SLOW_FORM.match(qual)]
    assert not slow, slow
