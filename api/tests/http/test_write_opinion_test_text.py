"""The legal test in the expert's own words (`opinions.test_text`, data dictionary; screen 15).

The workspace shows the packs' legal test as reference text; the expert writes the test they apply in
their own words beside it. It is saved with the opinion, sealed under the case key, kept in every saved
version and brought back by a restore. Leaving it out saves none.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from tests.fixtures.write.world import Reads, World, app_for, build
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

FIRST = "Would a responsible body zzq have acted the same way?"
SECOND = "Was the care below the standard zzq of the field?"


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


async def save(client: Any, w: World, version: int, **extra: Any) -> Any:
    payload = {"body": "My view.", "citation_refs": [], "view": None, "provisional": False, **extra}
    return await client.put(
        w.url(f"questions/{w.question}/opinion"), json=payload, headers={"If-Match": f'"{version}"'}
    )


async def test_the_test_text_is_saved_sealed_kept_in_versions_and_restored(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    expert = await client_for(world.expert)
    first = await save(expert, world, 0, test_text=FIRST)
    assert first.status_code == 200, first.text
    assert first.json()["test_text"] == FIRST
    second = (await save(expert, world, 1, test_text=SECOND)).json()
    assert second["test_text"] == SECOND

    sealed = admin.execute("select test_text from app.opinions where id = %s", (second["id"],)).fetchone()
    assert sealed is not None and b"zzq" not in bytes(sealed[0])
    kept = admin.execute("select test_text from app.opinion_versions where opinion_id = %s", (second["id"],))
    assert all(r[0] is not None and b"zzq" not in bytes(r[0]) for r in kept.fetchall())

    versions = (await expert.get(world.url(f"opinions/{second['id']}/versions"))).json()["versions"]
    assert [v["test_text"] for v in versions] == [SECOND, FIRST]
    restored = await expert.post(world.url(f"opinions/{second['id']}/versions/1/restore"), headers={"If-Match": '"2"'})
    assert restored.status_code == 200, restored.text
    assert restored.json()["test_text"] == FIRST
    got = (await expert.get(world.url(f"questions/{world.question}/workspace"))).json()
    assert got["opinion"]["test_text"] == FIRST


async def test_no_test_text_saves_none_and_a_long_one_is_refused(client_for: ClientFor, world: World) -> None:
    expert = await client_for(world.expert)
    assert (await save(expert, world, 0, test_text="x" * 2001)).status_code == 422
    saved = await save(expert, world, 0)
    assert saved.status_code == 200, saved.text
    assert saved.json()["test_text"] is None
