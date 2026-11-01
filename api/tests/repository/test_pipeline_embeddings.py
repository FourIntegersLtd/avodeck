"""Slice A: every page gets an embedding at the end of processing (brief step "embeddings", architecture 6.1 M7).

- After the bundle job, each page has one `page_embeddings` row: the case, the embedder's model name
  and a unit vector of the embedder's size. The cell's embedder (the recorded stand-in locally) is used.
- Only text that has been through `pii.strip` is embedded (rule 7): the names the case knows never
  reach the embedder.
- Running again embeds nothing more; a page whose embedding came from another model is embedded again
  (architecture 10: search stores the model that produced it, queries use current rows only).
Runs under the launch and the fixture packs. Nothing here prints page text (rule 6).
"""

from __future__ import annotations

import math
from collections.abc import AsyncIterator, Sequence
from typing import Any

import psycopg
import pytest

from app.core.ai import pii
from app.core.ai.embed import RecordedEmbedder
from app.core.db import Database
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import Harness, harness, text_pdf
from tests.support.ai import ScriptedBedrock, response
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

NAME = "Quillon Vexmoor"  # made up; planted on every page
PAGES = [[f"Synthetic {word} entry for {NAME}"] for word in ("alpha", "bravo", "charlie")]


class SpyEmbedder(RecordedEmbedder):
    """The recorded embedder, keeping what it was given (in memory only, never printed)."""

    def __init__(self) -> None:
        self.given: list[Any] = []
        self.calls: list[int] = []

    def embed(self, texts: Sequence[pii.Stripped]) -> list[tuple[float, ...]]:
        self.given.extend(texts)
        self.calls.append(len(texts))
        return super().embed(texts)


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


def answer(pack: Any) -> Any:
    first = embedded_words(split_pages(text_pdf(PAGES[:1]))[0]).words[0]
    return response(
        {
            "source_type": pack.source_types()[0]["key"],
            "printed_dates": [],
            "facts": [
                {
                    "kind": pack.fact_kinds()[0]["key"],
                    "clinician_role": None,
                    "fields": {},
                    "value": "Synthetic",
                    "word_ids": [first.id],
                }
            ],
            "references_out": [],
            "availability_evidence": [],
            "actions_requested_of_clinician": [],
            "entries": [],
        }
    )


@pytest.fixture(params=BOTH, ids=lambda c: c.codes[0])
async def h(
    request: pytest.FixtureRequest,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    combo: Combo = request.param
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    pack = default_library().combine(*combo.codes)
    spy = SpyEmbedder()
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
        names=[NAME],
        embedder=spy,
    ) as running:
        running.spy = spy  # type: ignore[attr-defined]
        await running.upload(text_pdf(PAGES))
        assert set((await running.drain()).values()) == {"done"}
        yield running


def embeddings(h: Harness) -> list[dict[str, Any]]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    return cur.execute(
        "select page_id, bundle_id, model, embedding::text as vector from app.page_embeddings where bundle_id = "
        "(select bundle_id from app.cases where id = %s)",
        (h.case_id,),
    ).fetchall()


def vector(row: dict[str, Any]) -> list[float]:
    return [float(x) for x in row["vector"].strip("[]").split(",")]


async def test_every_page_gets_one_embedding_from_the_cells_embedder(h: Harness) -> None:
    rows = embeddings(h)
    assert sorted(r["page_id"] for r in rows) == sorted(p["id"] for p in h.pages())
    for row in rows:
        assert row["case_id"] == h.case_id
        assert row["model"] == RecordedEmbedder.model
        values = vector(row)
        assert len(values) == RecordedEmbedder.dimensions
        assert abs(math.sqrt(sum(v * v for v in values)) - 1.0) < 1e-3


async def test_only_stripped_text_reaches_the_embedder(h: Harness) -> None:
    given = h.spy.given  # type: ignore[attr-defined]
    assert len(given) == len(PAGES)
    assert all(isinstance(t, pii.Stripped) for t in given)
    leaked = any(part.lower() in t.text.lower() for t in given for part in NAME.split())
    assert not leaked, "a name the case knows reached the embedder"


async def test_running_again_embeds_nothing_more(h: Harness) -> None:
    before = len(h.spy.given)  # type: ignore[attr-defined]
    await h.enqueue("process_bundle")
    await h.drain()
    assert len(h.spy.given) == before  # type: ignore[attr-defined]
    assert len(embeddings(h)) == len(PAGES)


async def test_a_page_embedded_by_another_model_is_embedded_again(h: Harness) -> None:
    page = h.pages()[0]["id"]
    h.admin.execute("update app.page_embeddings set model = 'zz-older-model' where page_id = %s", (page,))
    before = len(h.spy.given)  # type: ignore[attr-defined]
    await h.enqueue("process_bundle")
    await h.drain()
    assert len(h.spy.given) == before + 1  # type: ignore[attr-defined]
    assert {r["model"] for r in embeddings(h)} == {RecordedEmbedder.model}


async def test_the_pages_are_embedded_in_one_batch_not_one_call_each(h: Harness) -> None:
    assert h.spy.calls == [len(PAGES)]  # type: ignore[attr-defined]
