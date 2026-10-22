"""Rule 9 for organisations: the "Organisation actions" table in docs/spec/permissions.md, every cell.

`require_org_access(action)` (core/auth/org_access.py) is the only organisation access check. The
expected answers are read from the document. Someone who is not a current member (never joined,
left, account closed, or the organisation does not exist) gets 404; a member whose role lacks the
action, 403. 🔒 actions also need MFA in the last 10 minutes, checked after access.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.auth.org_access import check_org_access, require_org_access
from app.core.auth.permissions import ORG_ACTIONS, ORG_ROLES, org_allowed
from app.core.auth.session import Session
from app.core.db import Database
from app.core.errors import ApiError
from tests.support.db import scope_for
from tests.support.seed import Seed

REPO = Path(__file__).resolve().parents[3]
COLUMNS = ("owner", "admin", "expert", "assistant", "viewer")
NOT_ALLOWED = ("—", "own and assigned", "assigned")


@dataclass(frozen=True)
class OrgRow:
    cells: dict[str, str]
    step_up: bool


def _doc() -> dict[str, OrgRow]:
    text = (REPO / "docs/spec/permissions.md").read_text()
    section = text.split("## Organisation actions", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, OrgRow] = {}
    for line in section.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        assert len(cells) == 1 + len(COLUMNS), line
        for action in re.findall(r"`([a-z_.]+)`", cells[0]):
            rows[action] = OrgRow(dict(zip(COLUMNS, cells[1:], strict=True)), "🔒" in cells[0])
    return rows


DOC = _doc()


def expected(action: str, role: str) -> bool:
    cell = DOC[action].cells[role]
    if cell.startswith("✓"):
        return True
    assert cell in NOT_ALLOWED, f"unknown cell text {cell!r}: teach this test what it means first"
    return False


def test_the_document_lists_organisation_actions() -> None:
    assert len(DOC) >= 8


def test_code_knows_exactly_the_documented_organisation_actions() -> None:
    assert set(ORG_ACTIONS) == set(DOC)
    assert set(ORG_ROLES) == set(COLUMNS)


@pytest.mark.parametrize("action", sorted(DOC))
def test_step_up_marks_match_the_document(action: str) -> None:
    assert ORG_ACTIONS[action].step_up is DOC[action].step_up


@pytest.mark.parametrize("action", sorted(DOC))
def test_every_cell_in_code_matches_the_document(action: str) -> None:
    assert {r for r in ORG_ROLES if org_allowed(action, r)} == {r for r in COLUMNS if expected(action, r)}
    assert org_allowed(action, None) is False


def test_unknown_organisation_actions_are_refused() -> None:
    assert org_allowed("org.anything_else", "owner") is False
    with pytest.raises(ValueError):
        require_org_access("org.anything_else")


# --- end to end against real rows -------------------------------------------------------------------


@dataclass
class Team:
    org: uuid.UUID
    people: dict[str, uuid.UUID]


@pytest.fixture
def team(seed: Seed) -> Team:
    people = {name: seed.person() for name in (*ORG_ROLES, "left", "closed", "outsider")}
    members = {people[r]: r for r in ORG_ROLES}
    members[people["left"]] = "admin"
    members[people["closed"]] = "owner"
    org = seed.org(kind="team", members=members)
    seed.end_membership(org, people["left"])
    seed.close_account(people["closed"])
    seed.org(kind="personal", members={people["outsider"]: "owner"})
    return Team(org, people)


pytestmark_anyio = pytest.mark.anyio


@pytestmark_anyio
@pytest.mark.parametrize("role", ORG_ROLES)
async def test_every_cell_end_to_end(api_db: Database, team: Team, role: str) -> None:
    for action in sorted(DOC):
        async with api_db.request(scope_for(team.people[role])) as conn:
            if expected(action, role):
                access = await check_org_access(conn, team.org, action)
                assert (access.org_id, access.role, access.kind) == (team.org, role, "team")
                assert action in access.allowed_actions
            else:
                with pytest.raises(ApiError) as err:
                    await check_org_access(conn, team.org, action)
                assert err.value.status == 403, (role, action)


@pytestmark_anyio
@pytest.mark.parametrize("who", ["left", "closed", "outsider"])
async def test_anyone_not_a_current_member_gets_404(api_db: Database, team: Team, who: str) -> None:
    for action in sorted(DOC):
        async with api_db.request(scope_for(team.people[who])) as conn:
            with pytest.raises(ApiError) as err:
                await check_org_access(conn, team.org, action)
        assert (err.value.status, err.value.code) == (404, "not_found"), (who, action)


@pytestmark_anyio
async def test_an_organisation_that_does_not_exist_looks_the_same(api_db: Database, team: Team) -> None:
    async with api_db.request(scope_for(team.people["owner"])) as conn:
        with pytest.raises(ApiError) as err:
            await check_org_access(conn, uuid.uuid4(), "org.view_members")
    assert (err.value.status, err.value.code) == (404, "not_found")


def _request(person: uuid.UUID, *, mfa_age: timedelta) -> Any:
    now = datetime.now(UTC)
    session = Session(
        id=uuid.uuid4(),
        person_id=person,
        email="someone@example.test",
        full_name=None,
        csrf_hash="0" * 64,
        aal="aal2",
        mfa_enrolled=True,
        mfa_verified_at=now - mfa_age,
        last_seen_at=now,
        auth_access_token=None,
        auth_refresh_token=None,
        auth_expires_at=None,
    )
    return SimpleNamespace(state=SimpleNamespace(session=session))


@pytestmark_anyio
async def test_risky_organisation_actions_need_step_up_after_the_access_check(api_db: Database, team: Team) -> None:
    close = require_org_access("org.close")
    assert (close.org_action, close.step_up) == ("org.close", True)
    assert require_org_access("org.view_members").step_up is False
    stale = timedelta(minutes=11)
    async with api_db.request(scope_for(team.people["owner"])) as conn:
        access = await close(_request(team.people["owner"], mfa_age=timedelta(0)), team.org, conn)
        assert access.role == "owner"
        with pytest.raises(ApiError) as err:
            await close(_request(team.people["owner"], mfa_age=stale), team.org, conn)
        assert (err.value.status, err.value.code) == (403, "step_up_required")
        await require_org_access("org.view_members")(_request(team.people["owner"], mfa_age=stale), team.org, conn)
    async with api_db.request(scope_for(team.people["outsider"])) as conn:
        with pytest.raises(ApiError) as hidden:
            await close(_request(team.people["outsider"], mfa_age=stale), team.org, conn)
    assert hidden.value.status == 404
