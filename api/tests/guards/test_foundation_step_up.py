"""Rule 10: step-up. Risky actions (🔒) need MFA completed in the last 10 minutes (`aal2`).

The route table is scanned: every route that depends on `require_step_up`, or on
`require_case_access` for a 🔒 action in docs/spec/permissions.md, refuses a session whose last
MFA was 11 minutes ago with 403 `step_up_required`, and accepts one that has just done MFA.
"""

from __future__ import annotations

import re
import uuid
from datetime import timedelta
from typing import Any

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.core.auth.access import require_case_access
from app.core.auth.permissions import CASE_ACTIONS
from app.main import create_app
from tests.support.db import ClientFor
from tests.support.seed import Seed

# Person-level 🔒 actions (permissions.md "Person actions") built so far.
KNOWN_STEP_UP_ROUTES = {
    ("POST", "/v1/me/sessions/revoke-all"),
    ("POST", "/v1/auth/mfa/recovery-codes"),
    ("POST", "/v1/me/email-change"),
}


def needs_step_up(dependant: Dependant) -> bool:
    return any(getattr(d.call, "step_up", False) is True or needs_step_up(d) for d in dependant.dependencies)


def step_up_routes() -> list[tuple[str, str]]:
    return sorted(
        (method, r.path)
        for r in create_app().routes
        if isinstance(r, APIRoute) and needs_step_up(r.dependant)
        for method in r.methods or ()
    )


def test_the_risky_person_actions_need_step_up() -> None:
    assert KNOWN_STEP_UP_ROUTES <= set(step_up_routes())


@pytest.mark.parametrize("action", sorted(CASE_ACTIONS))
def test_case_access_for_a_locked_action_carries_step_up(action: str) -> None:
    assert getattr(require_case_access(action), "step_up", None) is CASE_ACTIONS[action].step_up


def _fill(path: str, case: uuid.UUID) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(case if m.group(1) == "case_id" else uuid.uuid4()), path)


@pytest.mark.anyio
@pytest.mark.parametrize(("method", "path"), step_up_routes())
async def test_step_up_route_refuses_mfa_older_than_ten_minutes(
    client_for: ClientFor, seed: Seed, method: str, path: str
) -> None:
    person = seed.person()
    case = seed.case(seed.org(kind="personal", members={person: "owner"}), person)
    client = await client_for(person, mfa_age=timedelta(minutes=11), **{"if-match": '"1"'})
    kwargs: dict[str, Any] = {} if method in ("GET", "DELETE") else {"json": {}}
    resp = await client.request(method, _fill(path, case), **kwargs)
    assert resp.status_code == 403, (method, path, resp.status_code)
    assert resp.json()["error"]["code"] == "step_up_required"


@pytest.mark.anyio
async def test_mfa_just_completed_passes_step_up(client_for: ClientFor, seed: Seed) -> None:
    person = seed.person()
    resp = await (await client_for(person, mfa_age=timedelta(minutes=9))).post("/v1/me/sessions/revoke-all")
    assert resp.status_code == 204, resp.text


@pytest.mark.anyio
async def test_a_session_that_never_completed_mfa_cannot_step_up(client_for: ClientFor, seed: Seed) -> None:
    resp = await (await client_for(seed.person(), aal="aal1", mfa_age=None)).post("/v1/me/sessions/revoke-all")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "mfa_required"
