"""Slice F, the expert's own words for the report sections that are not opinions (screen 16), and the
court's required contents they tick as addressed.

A section's words are the named expert's (`opinion.write`), saved sealed with citations in the same shape
as opinions; a citation must be to a citable fact (a checked fact on a page the expert has read). Opinions
are written on screen 15, declarations come from the pack and the generated appendices from Avodeck, so
those keys are refused. Required contents come from the jurisdiction pack; a key not in the pack is refused.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.report.jobs import build_report
from app.features.report.services import ReportServices, configure
from tests.fixtures.report.world import CRYPTO, Reads, World, app_for, body_paragraphs, build, cite
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: CaseFileStorage) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads, storage)


@pytest.fixture
def services(reads: Reads, storage: CaseFileStorage) -> Iterator[None]:
    configure(ReportServices(crypto=CRYPTO, storage=storage, reads=reads))
    yield
    configure(None)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


def writable(combo: Combo) -> str:
    """The first pack section the expert writes in their own words (not opinions or declarations)."""
    keys = [s["key"] for s in default_library().combine(*combo.codes).report_sections()]
    return str(next(k for k in keys if k not in ("opinion", "declarations")))


@pytest.mark.usefixtures("services")
async def test_the_expert_saves_a_section_in_their_own_words_and_the_build_uses_it(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    world: World,
) -> None:
    key = writable(world.combo)
    client = await client_for(world.expert)
    saved = await client.put(
        world.url(f"report/sections/{key}"),
        json={
            "body": "I was asked to look at the plan [[cite:1]].",
            "citations": [cite(world.plan, world.pages[0], "review 3/52")],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["version"] == 1
    again = await client.put(world.url(f"report/sections/{key}"), json={"body": "No citations now.", "citations": []})
    assert again.status_code == 428, "a second save names the version it replaces"
    again = await client.put(
        world.url(f"report/sections/{key}"),
        json={
            "body": "I was asked to look at the plan [[cite:1]].",
            "citations": [cite(world.plan, world.pages[0], "review 3/52")],
        },
        headers={"If-Match": saved.headers["etag"]},
    )
    assert again.status_code == 200 and again.json()["version"] == 2
    shown = await client.get(world.url(f"report/sections/{key}"))
    assert shown.json()["body"] == "I was asked to look at the plan [[cite:1]]."
    stored = admin.execute(
        "select body from app.report_section_texts where case_id = %s and section = %s", (world.case, key)
    ).fetchone()
    assert stored is not None and b"look at the plan" not in bytes(stored[0]), "sealed under the case key"

    queued = await client.post(world.url("report/build"))
    await build_report(JobContext(worker_db, uuid.UUID(queued.json()["job_id"]), world.case, "build_report", 1))
    path = admin.execute("select docx_path from app.report_versions where case_id = %s", (world.case,)).fetchone()
    assert path is not None
    data = await CRYPTO.decrypt(aconn, world.case, await storage.get(path[0]), purpose=path[0])
    words = " ".join(t for _, t, tag in body_paragraphs(data) if tag == f"section:{key}")
    assert "I was asked to look at the plan [p.1]." in words


async def test_section_words_cite_only_checked_facts_on_read_pages(client_for: ClientFor, world: World) -> None:
    key = writable(world.combo)
    client = await client_for(world.expert)
    unread = await client.put(
        world.url(f"report/sections/{key}"),
        json={"body": "Later [[cite:1]].", "citations": [cite(world.unread, world.pages[2], "seen")]},
    )
    assert unread.status_code == 409 and unread.json()["error"]["code"] == "not_citable"
    wrong_quote = await client.put(
        world.url(f"report/sections/{key}"),
        json={"body": "Plan [[cite:1]].", "citations": [cite(world.plan, world.pages[0], "not in the fact")]},
    )
    assert wrong_quote.status_code == 409 and wrong_quote.json()["error"]["code"] == "not_citable"
    markers = await client.put(
        world.url(f"report/sections/{key}"),
        json={"body": "No marker here.", "citations": [cite(world.plan, world.pages[0], "review 3/52")]},
    )
    assert markers.status_code == 422


async def test_opinions_declarations_and_built_appendices_are_not_written_here(
    client_for: ClientFor, world: World
) -> None:
    client = await client_for(world.expert)
    for key in ("opinion", "declarations", "methodology", "not_a_section"):
        answer = await client.put(world.url(f"report/sections/{key}"), json={"body": "Words.", "citations": []})
        assert answer.status_code == 422, key


async def test_only_the_named_expert_writes_section_words(client_for: ClientFor, world: World) -> None:
    key = writable(world.combo)
    body = {"body": "Words.", "citations": []}
    assert (
        await (await client_for(world.assistant)).put(world.url(f"report/sections/{key}"), json=body)
    ).status_code == 403
    assert (
        await (await client_for(world.outsider)).put(world.url(f"report/sections/{key}"), json=body)
    ).status_code == 404
    assert (await (await client_for(world.viewer)).get(world.url(f"report/sections/{key}"))).status_code == 404


async def test_required_contents_come_from_the_pack_and_are_ticked_by_the_expert(
    client_for: ClientFor, world: World
) -> None:
    items = default_library().combine(*world.combo.codes).compliance_checklist()["items"]
    client = await client_for(world.expert)
    before = (await client.get(world.url("report"))).json()["checklist"]
    assert [i["key"] for i in before["items"]] == [i["key"] for i in items]
    assert not any(i["done"] for i in before["items"])
    settings = await client.get(world.url("report/settings"))
    ticked = await client.patch(
        world.url("report/settings"),
        json={"addressed": [items[0]["key"]]},
        headers={"If-Match": settings.headers["etag"]},
    )
    assert ticked.status_code == 200, ticked.text
    after = (await client.get(world.url("report"))).json()["checklist"]
    assert [i["done"] for i in after["items"]] == [True] + [False] * (len(items) - 1)
    refused = await client.patch(
        world.url("report/settings"),
        json={"addressed": ["not_a_pack_item"]},
        headers={"If-Match": ticked.headers["etag"]},
    )
    assert refused.status_code == 422
