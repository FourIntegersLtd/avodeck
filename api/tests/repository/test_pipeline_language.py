"""Slice A increment 11d: pages not in the pack's language (F37, architecture 10.3).

- Each page with enough words gets `pages.language`; a page whose language is not the pack's is
  listed in the bundle summary (`foreign_language`) and in the request draft
  (`translation_page_ids` / `translation_page_nos`), so a certified translation can be asked for.
- Nothing is translated: the page is read and extracted like any other, with one model call.
- A page with too few words gets no language and is not listed.
Runs under the launch and the fixture packs. The pages are made up.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_quarantine import answer, summary
from tests.repository.test_pipeline_support import Harness, harness, t07b_requests, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ENGLISH = [
    "Synthetic alpha entry seen today with a cough for three days",
    "No fever and eating well and the chest was clear on examination",
    "Advised fluids and rest and to come back if it gets any worse",
]
WELSH = [
    "Synthetic annwyl feddyg diolch am eich llythyr ynglyn a'r claf",
    "Rydym wedi trefnu apwyntiad yn y clinig ddydd Llun nesaf",
    "Os oes gennych unrhyw gwestiynau cysylltwch a'r swyddfa os gwelwch yn dda",
    "Yn gywir yr adran gofal sylfaenol",
]
SHORT = ["Synthetic BP 120/80"]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture(params=BOTH, ids=lambda c: c.codes[0])
async def h(
    request: pytest.FixtureRequest,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> AsyncIterator[Harness]:
    combo: Combo = request.param
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    pack = default_library().combine(*combo.codes)
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([answer(pack)]),
    ) as running:
        running.pack = pack  # type: ignore[attr-defined]
        await running.upload(text_pdf([ENGLISH, WELSH, SHORT]))
        await running.drain()
        yield running


async def test_a_page_in_another_language_is_flagged_and_not_translated(h: Harness) -> None:
    pack_language = h.pack.language  # type: ignore[attr-defined]
    english, welsh, short = h.pages()
    assert (english["language"], welsh["language"], short["language"]) == (pack_language, "cy", None)

    flagged = (await summary(h))["foreign_language"]
    assert flagged == [{"page_id": str(welsh["id"]), "page_no": 2, "language": "cy"}]
    assert len(t07b_requests(h)) == 3, "a page was sent to the model more than once (or translated)"
    assert all(r.task_id in ("T-07b", "T-07c") for r in h.bedrock.requests)  # type: ignore[attr-defined]


async def test_the_request_draft_offers_a_certified_translation(h: Harness) -> None:
    got = await h.client.post(f"/v1/cases/{h.case_id}/instructing-party-requests/draft", json={})
    assert got.status_code == 200, got.status_code
    body = got.json()
    assert body["translation_page_nos"] == [2]
    assert body["translation_page_ids"] == [str(h.pages()[1]["id"])]
