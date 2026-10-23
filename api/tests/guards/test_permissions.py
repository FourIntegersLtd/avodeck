"""Rule 9: the permissions matrix in docs/spec/permissions.md, every cell (ADR 0013).

The expected answers are read from the document itself, so the code, the database function
`app.can_access_case()` and the document cannot drift apart. Cases run under the fixture packs
with two organisations, so a pass cannot depend on launch-pack words.

Reading a cell:
- case-role columns: "✓…" or "own reads only" = allowed (qualifiers are enforced by the slice
  service, not the access check); "—" = not allowed;
- support column: what Avodeck support may do under a live grant from the named expert (the case
  role `support`, never a `case_members` role);
- custodian column: what a records custodian may do on a served case transferred to them (the case
  role `custodian`, never a `case_members` role; migration 0031);
- organisation column: "✓…" = owner and admin, "✓ (team cases)" = owner and admin on team
  organisations only, "owner only…" = owner only, "—" = neither.
Not allowed but able to see the case = 403; unable to see it at all = 404.
"""

from __future__ import annotations

import itertools
import re
import uuid
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest

from app.core.auth.access import check_case_access
from app.core.auth.permissions import (
    CASE_ACTIONS,
    CASE_ROLES,
    CUSTODIAN_ACTIONS,
    CUSTODIAN_ROLE,
    ORG_ROLES,
    SUPPORT_ACTIONS,
    SUPPORT_ROLE,
    allowed,
)
from app.core.db import Database
from app.core.errors import ApiError
from tests.support.db import scope_for
from tests.support.seed import Seed

REPO = Path(__file__).resolve().parents[3]
DOC_COLUMNS = ("named_expert", "co_expert", "supervisor", "assistant", "viewer", "support", "custodian", "org")
ORG_KINDS = ("personal", "team")


@dataclass(frozen=True)
class DocRow:
    cells: dict[str, str]
    step_up: bool


def _doc_matrix() -> dict[str, DocRow]:
    text = (REPO / "docs/spec/permissions.md").read_text()
    section = text.split("## Case actions", 1)[1].split("\n## ", 1)[0]
    matrix: dict[str, DocRow] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 1 + len(DOC_COLUMNS), line
        for action, trailing in re.findall(r"`([a-z_.]+)`([^`]*)", cells[0]):
            matrix[action] = DocRow(dict(zip(DOC_COLUMNS, cells[1:], strict=True)), "🔒" in trailing)
    return matrix


DOC = _doc_matrix()


def _case_cell(cell: str) -> bool:
    if cell.startswith("✓") or cell.startswith("own reads only"):
        return True
    assert cell == "—", f"unknown cell text {cell!r}: teach this test what it means first"
    return False


def _org_cell(cell: str, org_role: str | None, org_kind: str) -> bool:
    if org_role not in ("owner", "admin"):
        return False  # org experts, assistants and viewers get nothing on cases they are not on
    if cell == "—":
        return False
    if cell.startswith("owner only"):
        return org_role == "owner"
    assert cell.startswith("✓"), f"unknown cell text {cell!r}: teach this test what it means first"
    return org_kind == "team" if "team cases" in cell else True


def expected(action: str, case_role: str | None, org_role: str | None, org_kind: str) -> bool:
    row = DOC[action].cells
    by_case = case_role is not None and _case_cell(row[case_role])
    return by_case or _org_cell(row["org"], org_role, org_kind)


# --- the matrix in code matches the document --------------------------------------------------


def test_the_document_lists_actions() -> None:
    assert len(DOC) >= 25


def test_code_knows_exactly_the_documented_actions() -> None:
    assert set(CASE_ACTIONS) == set(DOC)


def test_case_roles_and_org_roles_are_the_documented_ones() -> None:
    assert set(CASE_ROLES) == set(DOC_COLUMNS) - {"support", "custodian", "org"}
    assert SUPPORT_ROLE == "support" and SUPPORT_ROLE not in CASE_ROLES  # never a case_members role
    assert CUSTODIAN_ROLE == "custodian" and CUSTODIAN_ROLE not in CASE_ROLES
    assert set(ORG_ROLES) == {"owner", "admin", "expert", "assistant", "viewer"}


@pytest.mark.parametrize("action", sorted(DOC))
def test_step_up_marks_match_the_document(action: str) -> None:
    assert CASE_ACTIONS[action].step_up is DOC[action].step_up


COMBOS = list(itertools.product((None, *CASE_ROLES, SUPPORT_ROLE, CUSTODIAN_ROLE), (None, *ORG_ROLES), ORG_KINDS))
# `app.case_action_allowed()` knows the case_members roles; `app.case_role_allowed()` adds support and
# custodian to it.
MEMBER_COMBOS = [c for c in COMBOS if c[0] not in (SUPPORT_ROLE, CUSTODIAN_ROLE)]


@pytest.mark.parametrize("action", sorted(DOC))
def test_every_cell_in_code_matches_the_document(action: str) -> None:
    wrong = [
        (case_role, org_role, kind)
        for case_role, org_role, kind in COMBOS
        if allowed(action, case_role, org_role, kind) is not expected(action, case_role, org_role, kind)
    ]
    assert wrong == []


@pytest.mark.parametrize("action", ["link.decide", "finding.decide", "availability.override", "consultation.note"])
def test_understand_decisions_are_the_named_experts_alone(action: str) -> None:
    """Slice D: only the named expert accepts links, decides findings, corrects availability and keeps notes."""
    assert [r for r in CASE_ROLES if allowed(action, r, "owner", "team")] == ["named_expert"]


def test_suggesting_links_is_open_to_case_readers_but_not_viewers() -> None:
    assert [r for r in CASE_ROLES if allowed("link.suggest", r, None, "team")] == [
        r for r in CASE_ROLES if allowed("page.read", r, None, "team")
    ]
    assert not allowed("link.suggest", "viewer", "owner", "team")


def test_unknown_actions_are_refused() -> None:
    assert allowed("case.anything_else", "named_expert", "owner", "team") is False


def test_the_database_matrix_matches_the_code(admin: psycopg.Connection) -> None:
    rows = [(a, c, o, k) for a in sorted(DOC) for c, o, k in MEMBER_COMBOS]
    result = admin.execute(
        """
        select r.action, r.case_role, r.org_role, r.org_kind,
               app.case_action_allowed(r.action, r.case_role, r.org_role, r.org_kind)
        from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as r(action, case_role, org_role, org_kind)
        """,
        ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows]),
    ).fetchall()
    wrong = [r[:4] for r in result if r[4] is not allowed(r[0], r[1], r[2], r[3])]
    assert wrong == []


def test_the_database_matrix_with_support_matches_the_code(admin: psycopg.Connection) -> None:
    """`app.case_role_allowed()` is what `app.can_access_case()` asks: the matrix plus the support role."""
    rows = [(a, c, o, k) for a in sorted(DOC) for c, o, k in COMBOS]
    result = admin.execute(
        """
        select r.action, r.case_role, r.org_role, r.org_kind,
               app.case_role_allowed(r.action, r.case_role, r.org_role, r.org_kind)
        from unnest(%s::text[], %s::text[], %s::text[], %s::text[]) as r(action, case_role, org_role, org_kind)
        """,
        ([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows], [r[3] for r in rows]),
    ).fetchall()
    wrong = [r[:4] for r in result if r[4] is not allowed(r[0], r[1], r[2], r[3])]
    assert wrong == []


def test_support_never_writes() -> None:
    """Rule 9: under a grant, support only looks (S17)."""
    assert SUPPORT_ACTIONS == {"case.view_metadata", "case.view_content", "case.view_audit"}
    assert {a for a in CASE_ACTIONS if allowed(a, SUPPORT_ROLE, None, "team")} == SUPPORT_ACTIONS
    assert all(SUPPORT_ROLE not in rule.case_roles for rule in CASE_ACTIONS.values())  # not a case_members role


def test_a_custodian_only_reads() -> None:
    """Architecture 10.1 "Death or incapacity": a records custodian reads a served case, never writes."""
    assert CUSTODIAN_ACTIONS == {"case.view_metadata", "case.view_content", "case.view_audit", "case.view_certificate"}
    assert {a for a in CASE_ACTIONS if allowed(a, CUSTODIAN_ROLE, None, "team")} == CUSTODIAN_ACTIONS


# --- every cell, end to end against real rows -------------------------------------------------


@dataclass
class World:
    case: uuid.UUID
    people: dict[str, uuid.UUID]
    roles: dict[str, tuple[str | None, str | None]]  # who -> (case role, org role)


@pytest.fixture
def world(seed: Seed) -> World:
    """A team case with someone in every column, and an outsider from a second organisation.

    `support` is Avodeck support staff holding a live grant from the named expert."""
    people = {name: seed.person() for name in (*CASE_ROLES, SUPPORT_ROLE, "owner", "admin", "org_expert", "outsider")}
    org_roles = {
        "named_expert": "expert",
        "co_expert": "expert",
        "supervisor": "expert",
        "assistant": "assistant",
        "viewer": "viewer",
        "owner": "owner",
        "admin": "admin",
        "org_expert": "expert",
    }
    team = seed.org(kind="team", members={people[n]: r for n, r in org_roles.items()})
    seed.org(kind="personal", members={people["outsider"]: "owner"})
    case = seed.case(team, people["named_expert"], members={people[r]: r for r in CASE_ROLES if r != "named_expert"})
    seed.support_staff(people[SUPPORT_ROLE])
    seed.support_grant(case, people["named_expert"], granted_to=people[SUPPORT_ROLE])
    roles: dict[str, tuple[str | None, str | None]] = {
        n: (n if n in (*CASE_ROLES, SUPPORT_ROLE) else None, org_roles.get(n)) for n in people
    }
    return World(case, people, roles)


pytestmark_anyio = pytest.mark.anyio


@pytestmark_anyio
@pytest.mark.parametrize("who", [*CASE_ROLES, SUPPORT_ROLE, "owner", "admin", "org_expert", "outsider"])
async def test_every_cell_end_to_end(api_db: Database, world: World, who: str) -> None:
    case_role, org_role = world.roles[who]
    can_see = expected("case.view_metadata", case_role, org_role, "team")
    for action in sorted(DOC):
        want = expected(action, case_role, org_role, "team")
        async with api_db.request(scope_for(world.people[who])) as conn:
            cur = await conn.execute("select app.can_access_case(%s, %s)", (world.case, action))
            assert (await cur.fetchone()) == (want,), (who, action, "database")
            if want:
                access = await check_case_access(conn, world.case, action)
                assert action in access.allowed_actions
            else:
                with pytest.raises(ApiError) as err:
                    await check_case_access(conn, world.case, action)
                assert err.value.status == (403 if can_see else 404), (who, action)


@pytestmark_anyio
async def test_the_custodian_column_end_to_end(api_db: Database, seed: Seed) -> None:
    """Custody is only ever of a served case, so the custodian column is checked on one (migration 0031)."""
    expert, staff, custodian = seed.person(), seed.person(), seed.person()
    case = seed.case(seed.org(kind="team", members={expert: "expert"}), expert, status="served")
    seed.support_staff(staff)
    seed.records_custodian(expert, custodian)
    seed.custody_transfer(case, staff, to_person=custodian)
    for action in sorted(DOC):
        want = expected(action, CUSTODIAN_ROLE, None, "team")
        async with api_db.request(scope_for(custodian)) as conn:
            cur = await conn.execute("select app.can_access_case(%s, %s)", (case, action))
            assert (await cur.fetchone()) == (want,), (action, "database")
            if want:
                access = await check_case_access(conn, case, action)
                assert (access.case_role, action in access.allowed_actions) == (CUSTODIAN_ROLE, True)
            else:
                with pytest.raises(ApiError) as err:
                    await check_case_access(conn, case, action)
                assert err.value.status == 403, action


@pytestmark_anyio
async def test_a_case_that_does_not_exist_looks_the_same_as_one_you_cannot_see(api_db: Database, world: World) -> None:
    async with api_db.request(scope_for(world.people["outsider"])) as conn:
        with pytest.raises(ApiError) as missing:
            await check_case_access(conn, uuid.uuid4(), "case.view_metadata")
        with pytest.raises(ApiError) as hidden:
            await check_case_access(conn, world.case, "case.view_metadata")
    assert (missing.value.status, missing.value.code) == (hidden.value.status, hidden.value.code) == (404, "not_found")


@pytestmark_anyio
async def test_same_person_named_expert_on_one_case_and_assistant_on_another(api_db: Database, seed: Seed) -> None:
    """Scenario S1: rights follow the case, not the person."""
    person, other_expert = seed.person(), seed.person()
    personal = seed.org(kind="personal", members={person: "owner"})
    team = seed.org(kind="team", members={person: "assistant", other_expert: "expert"})
    own_case = seed.case(personal, person)
    team_case = seed.case(team, other_expert, members={person: "assistant"})
    async with api_db.request(scope_for(person)) as conn:
        assert "fact.verify" in (await check_case_access(conn, own_case, "fact.verify")).allowed_actions
        await check_case_access(conn, team_case, "case.upload")
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, team_case, "fact.verify")
    assert err.value.status == 403


@pytestmark_anyio
async def test_leaving_the_organisation_keeps_named_expert_rights_but_not_assistant_rights(
    api_db: Database, seed: Seed
) -> None:
    expert, assistant = seed.person(), seed.person()
    team = seed.org(kind="team", members={expert: "expert", assistant: "assistant", seed.person(): "owner"})
    case = seed.case(team, expert, members={assistant: "assistant"})
    seed.end_membership(team, expert)
    seed.end_membership(team, assistant)
    async with api_db.request(scope_for(expert)) as conn:
        await check_case_access(conn, case, "report.sign")
    async with api_db.request(scope_for(assistant)) as conn:
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, case, "case.view_metadata")
    assert err.value.status == 404


@pytestmark_anyio
async def test_a_removed_case_member_loses_access(api_db: Database, seed: Seed, admin: psycopg.Connection) -> None:
    expert, viewer = seed.person(), seed.person()
    team = seed.org(kind="team", members={expert: "owner", viewer: "viewer"})
    case = seed.case(team, expert, members={viewer: "viewer"})
    admin.execute("update app.case_members set removed_at = now() where case_id = %s", (case,))
    async with api_db.request(scope_for(viewer)) as conn:
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, case, "case.view_content")
    assert err.value.status == 404


@pytestmark_anyio
async def test_nobody_signed_in_can_access_nothing(api_dsn: str, world: World) -> None:
    async with await psycopg.AsyncConnection.connect(api_dsn) as conn:
        cur = await conn.execute("select app.can_access_case(%s, 'case.view_metadata')", (world.case,))
        assert await cur.fetchone() == (False,)


# --- the actions slice C needs (founder-approved, docs/slices/0-foundation.md "Changes for slice C") ---------


@pytest.mark.parametrize(
    ("action", "roles", "step_up"),
    [
        ("case.hold", {"named_expert", "assistant"}, False),
        ("conflict.declare", {"named_expert"}, False),
        ("case.change_jurisdiction", {"named_expert"}, True),
    ],
)
def test_the_setup_actions_are_in_the_matrix(action: str, roles: set[str], step_up: bool) -> None:
    rule = CASE_ACTIONS[action]
    assert (set(rule.case_roles), rule.org, rule.step_up) == (roles, "none", step_up)
    assert {r for r in CASE_ROLES if expected(action, r, None, "team")} == roles
    assert DOC[action].step_up is step_up
