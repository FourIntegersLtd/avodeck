"""Rule 9: every /v1/cases/{case_id}/... route calls require_case_access(action).

The route table is scanned automatically, so a new case route without the check fails here
without anyone having to remember to add a test. A person without access gets 404 from every
case route, exactly as if the case did not exist.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.core.auth.permissions import CASE_ACTIONS
from app.main import create_app
from tests.support.db import ClientFor
from tests.support.seed import Seed

CASE_PREFIX = "/v1/cases/{"


def case_routes() -> list[APIRoute]:
    return [r for r in create_app().routes if isinstance(r, APIRoute) and r.path.startswith(CASE_PREFIX)]


def access_actions(dependant: Dependant) -> list[str]:
    found: list[str] = []
    for dep in dependant.dependencies:
        action = getattr(dep.call, "case_action", None)
        if action is not None:
            found.append(action)
        found.extend(access_actions(dep))
    return found


def test_there_are_case_routes_to_check() -> None:
    assert case_routes(), "no /v1/cases/{case_id}/... routes found; the scan would prove nothing"


@pytest.mark.parametrize("route", case_routes(), ids=lambda r: f"{sorted(r.methods)} {r.path}")
def test_every_case_route_names_its_case_id_and_calls_require_case_access(route: APIRoute) -> None:
    assert route.path.startswith("/v1/cases/{case_id}"), "case routes take the case as {case_id}"
    actions = access_actions(route.dependant)
    assert actions, f"{route.path} does not depend on require_case_access(action)"
    assert set(actions) <= set(CASE_ACTIONS), f"unknown actions {set(actions) - set(CASE_ACTIONS)}"


def _fill(path: str, case: uuid.UUID) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(case if m.group(1) == "case_id" else uuid.uuid4()), path)


@pytest.mark.anyio
async def test_every_case_route_returns_404_to_a_person_without_access(client_for: ClientFor, seed: Seed) -> None:
    owner, outsider = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={owner: "owner"}), owner)
    seed.org(kind="personal", members={outsider: "owner"})
    client = await client_for(outsider, **{"if-match": '"1"', "idempotency-key": "k-guard"})
    for route in case_routes():
        for method in sorted(route.methods or ()):
            kwargs: dict[str, Any] = {} if method in ("GET", "DELETE", "HEAD") else {"json": {}}
            for target in (case, uuid.uuid4()):
                resp = await client.request(method, _fill(route.path, target), **kwargs)
                assert resp.status_code == 404, (method, route.path, resp.status_code)
                assert resp.json()["error"]["code"] == "not_found"
