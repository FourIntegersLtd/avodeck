"""Screen 02: T-02 suggests a report section for each heading of the expert's Word template (architecture
6.3, slice C brief).

The sections are the pack's `report_sections` keys plus `other` (rule 11). The answer is a suggestion
only: nothing is stored until the expert confirms it through `PATCH /v1/me/template/mapping`. A suggestion
for something that is not one of the template's headings, or a second one for the same heading, is refused.
Without a model the call says processing is paused instead of guessing (F24).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.core.ai.bedrock import RecordedBedrock
from app.core.ai.kit import AiKit
from app.core.ai.recordings import FixtureStore
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.setup.services import SetupServices, configure
from tests.support.ai import cell
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "setup"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
CELL = str(LAUNCH.raw("jurisdiction", "pack.yaml")["cell"])
# What a careful reader would map the fixture template's headings to (by paragraph index).
EXPECTED = {
    0: "other",
    1: "introduction",
    3: "instructions",
    5: "documents",
    7: "background",
    9: "what_happened",
    11: "opinion",
    13: "range_of_opinion",
    15: "summary",
    17: "declarations",
}


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=api_db,
        region=CELL,
        auth=SupabaseAuth(auth_settings(auth_env)),
        mailer=OutboxMailer(),
        storage=storage,
        crypto=CaseCrypto(LocalKeyProvider.generate()),
    )


def services(storage: CaseFileStorage, root: str = "recordings") -> RecordedBedrock:
    settings = cell()
    model = RecordedBedrock(settings, FixtureStore(FIXTURES / root))
    kit = AiKit(settings, model)
    configure(SetupServices(kit=kit, ocr=None, storage=storage, crypto=None, packs=default_library()))
    return model


@pytest.fixture
def model(storage: CaseFileStorage) -> Iterator[RecordedBedrock]:
    yield services(storage)
    configure(None)


@pytest.fixture
def expert(seed: Seed) -> uuid.UUID:
    person = seed.person()
    seed.org(kind="personal", members={person: "owner"})
    return person


async def upload_template(client: httpx.AsyncClient) -> httpx.Response:
    resp = await client.post(
        "/v1/me/template/file",
        params={"filename": "template.docx"},
        content=(FIXTURES / "template.docx").read_bytes(),
        headers={"content-type": DOCX},
    )
    assert resp.status_code == 201, resp.text
    return resp


async def test_each_heading_gets_a_suggested_section_that_is_not_saved(
    client_for: ClientFor, expert: uuid.UUID, model: RecordedBedrock, admin: object
) -> None:
    client = await client_for(expert)
    up = await upload_template(client)
    resp = await client.post("/v1/me/template/suggestions")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected"] == 0
    assert {s["index"]: s["section"] for s in body["suggestions"]} == EXPECTED
    assert all(s["reason"] for s in body["suggestions"])
    assert len(model.requests) == 1

    # A suggestion only: the saved mapping is unchanged until the expert confirms it.
    template = await client.get("/v1/me/template")
    assert template.json()["mapping"] == []
    confirmed = {"mapping": [{"index": s["index"], "section": s["section"]} for s in body["suggestions"]]}
    saved = await client.patch("/v1/me/template/mapping", json=confirmed, headers={"if-match": up.headers["etag"]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["mapping"] == confirmed["mapping"]


async def test_the_call_is_logged_against_the_person_without_its_content(
    client_for: ClientFor, expert: uuid.UUID, model: RecordedBedrock, admin: object
) -> None:
    import psycopg

    client = await client_for(expert)
    await upload_template(client)
    assert (await client.post("/v1/me/template/suggestions")).status_code == 200
    assert isinstance(admin, psycopg.Connection)
    rows = admin.execute(
        "select task_id, case_id, outcome from app.ai_calls where person_id = %s", (expert,)
    ).fetchall()
    assert rows == [("T-02", None, "ok")]


async def test_a_suggestion_for_no_heading_or_a_second_one_for_a_heading_is_refused(
    client_for: ClientFor, expert: uuid.UUID, storage: CaseFileStorage
) -> None:
    services(storage, root="recordings-paraphrased")
    try:
        client = await client_for(expert)
        await upload_template(client)
        resp = await client.post("/v1/me/template/suggestions")
    finally:
        configure(None)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rejected"] == 2
    assert {s["index"]: s["section"] for s in body["suggestions"]} == {1: "introduction", 3: "instructions"}


async def test_without_a_template_there_is_nothing_to_suggest(
    client_for: ClientFor, expert: uuid.UUID, model: RecordedBedrock
) -> None:
    client = await client_for(expert)
    assert (await client.post("/v1/me/template/suggestions")).status_code == 404
    assert model.requests == []


async def test_without_a_model_processing_is_paused(
    client_for: ClientFor, expert: uuid.UUID, storage: CaseFileStorage
) -> None:
    configure(SetupServices(kit=None, ocr=None, storage=storage, crypto=None, packs=default_library()))
    try:
        client = await client_for(expert)
        await upload_template(client)
        resp = await client.post("/v1/me/template/suggestions")
    finally:
        configure(None)
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "processing_paused"
