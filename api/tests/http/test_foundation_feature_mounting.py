"""Each slice's router is mounted by the app factory (architecture 8.4; `app.features.<name>.router`).

`create_app()` imports every package under `app.features` and mounts its `router` with `mount()`, so
its routes are in `app.routes` and the rule 9 guard scans them. A feature without a router is fine.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute

from app.core.db import Database
from app.main import create_app
from tests.guards.test_foundation_case_routes import access_actions
from tests.support.db import ClientFor
from tests.support.features import dummy_features
from tests.support.files import build_app
from tests.support.seed import Seed

PATH = "/v1/cases/{case_id}/zz-dummy"


@pytest.fixture
def features(tmp_path: Path) -> str:
    return dummy_features(tmp_path)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], features: str) -> FastAPI:
    return build_app(api_db, auth_env, features=features)


def test_the_app_package_for_features_exists() -> None:
    import app.features

    assert app.features.__path__


def test_a_features_router_is_mounted_and_checks_case_access(features: str) -> None:
    routes = [r for r in create_app(features=features).routes if isinstance(r, APIRoute) and r.path == PATH]
    assert len(routes) == 1
    assert access_actions(routes[0].dependant) == ["case.view_content"]


@pytest.mark.anyio
async def test_a_mounted_feature_route_answers_404_without_access_and_200_with(
    client_for: ClientFor, seed: Seed
) -> None:
    expert, outsider = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    seed.org(kind="personal", members={outsider: "owner"})
    path = PATH.replace("{case_id}", str(case))
    assert (await (await client_for(outsider)).get(path)).status_code == 404
    assert (await (await client_for(outsider)).get(PATH.replace("{case_id}", str(uuid.uuid4())))).status_code == 404
    resp = await (await client_for(expert)).get(path)
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"zz": "ok"}
