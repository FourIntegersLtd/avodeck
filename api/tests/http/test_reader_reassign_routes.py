"""Slice B, screen S09: after the named expert changes, the page says why suggestions are back.

`GET /v1/cases/{id}/pages/{n}/facts` also gives `reassigned`: true when a fact on the page is `suggested`
again because the named expert changed (its latest `fact_verifications` row is the system's `reset`). It is
false otherwise, and false again once the new expert has checked every such fact. Still 423 until read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.http import etag
from tests.fixtures.stub_facts import load_stub_facts
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    from tests.support.files import build_app

    return build_app(api_db, auth_env, crypto=CRYPTO)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def world(seed: Seed, aconn: psycopg.AsyncConnection, client_for: Any) -> dict[str, Any]:
    old, new = seed.person(), seed.person()
    case = seed.case(
        seed.org(kind="team", members={old: "expert", new: "expert"}), old, status="active", packs=LAUNCH.case_row()
    )
    await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO)
    w = {"case": case, "aconn": aconn, "old": await client_for(old), "new": await client_for(new), "new_id": new}
    assert (await w["old"].post(f"/v1/cases/{case}/pages/118/read", json={"dwell_seconds": 20})).status_code == 200
    return w


async def page_facts(client: Any, case: Any) -> dict[str, Any]:
    r = await client.get(f"/v1/cases/{case}/pages/118/facts")
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


async def test_not_reassigned_before_any_change(world: dict[str, Any]) -> None:
    assert (await page_facts(world["old"], world["case"]))["reassigned"] is False


async def test_reassigned_after_the_named_expert_changed(world: dict[str, Any]) -> None:
    case = world["case"]
    fact = next(f for f in (await page_facts(world["old"], case))["facts"] if f["value"] and f["word_ids"])
    r = await world["old"].post(
        f"/v1/cases/{case}/facts/{fact['id']}/verify", json={"action": "correct"}, headers={"if-match": etag(fact["version"])}
    )
    assert r.status_code == 200, r.text
    await world["aconn"].execute("update app.cases set named_expert_id = %s where id = %s", (world["new_id"], case))
    blind = await world["new"].get(f"/v1/cases/{case}/pages/118/facts")
    assert blind.status_code == 423
    assert (await world["new"].post(f"/v1/cases/{case}/pages/118/read", json={"dwell_seconds": 20})).status_code == 200
    out = await page_facts(world["new"], case)
    assert out["reassigned"] is True
    assert all(f["status"] == "suggested" for f in out["facts"])


async def test_not_reassigned_once_the_new_expert_checked_the_reset_facts(world: dict[str, Any]) -> None:
    case = world["case"]
    fact = next(f for f in (await page_facts(world["old"], case))["facts"] if f["value"] and f["word_ids"])
    await world["old"].post(
        f"/v1/cases/{case}/facts/{fact['id']}/verify", json={"action": "correct"}, headers={"if-match": etag(fact["version"])}
    )
    await world["aconn"].execute("update app.cases set named_expert_id = %s where id = %s", (world["new_id"], case))
    await world["new"].post(f"/v1/cases/{case}/pages/118/read", json={"dwell_seconds": 20})
    out = await page_facts(world["new"], case)
    for f in out["facts"]:
        if f["status"] == "suggested" and f["value"] and f["word_ids"]:
            r = await world["new"].post(
                f"/v1/cases/{case}/facts/{f['id']}/verify", json={"action": "correct"}, headers={"if-match": etag(f["version"])}
            )
            assert r.status_code == 200, r.text
    assert (await page_facts(world["new"], case))["reassigned"] is False
