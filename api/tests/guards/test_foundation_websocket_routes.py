"""Rules 4 and 9 for WebSocket routes (dictation and anything after it).

The CSRF and case-route guards scan HTTP routes only; this one scans WebSocket routes. Each one
must be a case route that depends on require_case_access's WebSocket form (a `case_action`), and
must refuse, before accepting, anyone without a session (4401) or without access to the case (4404).
A browser always sends Origin on a WebSocket; the session check refuses any other origin.
"""

from __future__ import annotations

import re
import uuid

import pytest
from fastapi.routing import APIWebSocketRoute

from app.core.auth.permissions import CASE_ACTIONS
from app.core.db import Database
from app.main import create_app
from tests.guards.test_foundation_case_routes import access_actions
from tests.support.auth import cookie_header
from tests.support.seed import Seed
from tests.support.ws import ws_connect

ORIGIN = "https://app.test"


def websocket_routes() -> list[APIWebSocketRoute]:
    return [r for r in create_app().routes if isinstance(r, APIWebSocketRoute)]


def test_there_are_websocket_routes_to_check() -> None:
    assert websocket_routes(), "no WebSocket routes found; the scan would prove nothing"


@pytest.mark.parametrize("route", websocket_routes(), ids=lambda r: r.path)
def test_every_websocket_route_is_a_case_route_that_checks_case_access(route: APIWebSocketRoute) -> None:
    assert route.path.startswith("/v1/cases/{case_id}/"), "WebSocket routes act on one case"
    actions = access_actions(route.dependant)
    assert actions, f"{route.path} does not check case access"
    assert set(actions) <= set(CASE_ACTIONS)


def _fill(path: str, case: uuid.UUID) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(case if m.group(1) == "case_id" else uuid.uuid4()), path)


@pytest.mark.anyio
async def test_every_websocket_route_refuses_no_session_and_no_access_before_accepting(
    seed: Seed, api_db: Database
) -> None:
    app = create_app(database=api_db)
    owner, outsider = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={owner: "owner"}), owner)
    seed.org(kind="personal", members={outsider: "owner"})
    app.state.app_origins = (ORIGIN,)
    for route in websocket_routes():
        path = _fill(route.path, case) + "?context=guard"
        async with ws_connect(app, path, [("origin", ORIGIN)]) as ws:
            assert (ws.accepted, ws.close_code) == (False, 4401), route.path
        signed_in = [("origin", ORIGIN), ("cookie", cookie_header(seed.session(outsider)))]
        async with ws_connect(app, path, signed_in) as ws:
            assert (ws.accepted, ws.close_code) == (False, 4404), route.path
