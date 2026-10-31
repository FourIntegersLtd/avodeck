"""Slice A: split a two-page scan (ADR 0019, plan item 8).

- `POST /v1/cases/{id}/pages/{page_id}/split` `{axis, at, first}` (page.replace) cuts the page's PDF
  into two halves, stores them as a new generated document and queues the pipeline. The halves are
  numbered after the case's last page (first half, then second; nothing is renumbered), each is read
  again with its own words, and each records which page it was split from (`pages.split_of_page_id`).
- The original stays: number, picture, facts. Checked facts are untouched; its unchecked suggestions
  are rejected once the halves are numbered. The halves start unread (blind read, 423).
- A page set aside or already split cannot be split again (409); a half can be (it is a page).
  Unknown pages 404, viewers 403, outsiders 404. Audited. The bundle summary counts splits.
Runs under the launch and the fixture packs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_quarantine import answer, audited
from tests.repository.test_pipeline_support import Harness, facts_of, harness, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


SPREAD = 2
BUNDLE = [["Synthetic alpha entry"], ["Synthetic bravo left Synthetic bravo right"], ["Synthetic charlie entry"]]
CUT = {"axis": "vertical", "at": 0.5, "first": "left"}


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
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
    storage: CaseFileStorage,
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
        bedrock=ScriptedBedrock([answer(pack)] * 6),
    ) as running:
        await running.upload(text_pdf(BUNDLE))
        await running.drain()
        yield running


def page(h: Harness, n: int) -> dict[str, Any]:
    return next(p for p in h.pages() if p["bundle_page_no"] == n)


def facts_on(h: Harness, n: int) -> list[dict[str, Any]]:
    return [f for f in facts_of(h) if f["bundle_page_no"] == n]


def split_path(h: Harness, page_id: uuid.UUID) -> str:
    return f"/v1/cases/{h.case_id}/pages/{page_id}/split"


async def test_a_split_adds_two_linked_pages_and_renumbers_nothing(h: Harness) -> None:
    before = {p["id"]: p["bundle_page_no"] for p in h.pages()}
    (check,) = facts_on(h, SPREAD)
    h.admin.execute("update app.facts set status = 'verified' where id = %s", (check["id"],))
    original = page(h, SPREAD)

    sent = await h.client.post(split_path(h, original["id"]), json=CUT)
    assert sent.status_code == 202, sent.status_code
    await h.drain()

    pages = h.pages()
    assert {p["id"]: p["bundle_page_no"] for p in pages if p["id"] in before} == before, "a page was renumbered"
    assert [p["bundle_page_no"] for p in pages] == list(range(1, len(BUNDLE) + 3))
    first, second = page(h, len(BUNDLE) + 1), page(h, len(BUNDLE) + 2)
    assert first["split_of_page_id"] == original["id"] and second["split_of_page_id"] == original["id"]
    assert first["document_id"] == second["document_id"] != original["document_id"]
    assert page(h, SPREAD)["split_of_page_id"] is None and page(h, SPREAD)["superseded_by_page_id"] is None
    assert audited(h, "page.split") == 1
    thumb = await h.client.get(f"/v1/cases/{h.case_id}/pages/{SPREAD}/thumbnail")
    assert thumb.status_code == 200, "the original is no longer readable"

    (still,) = facts_on(h, SPREAD)
    assert (still["id"], still["status"]) == (check["id"], "verified"), "a checked fact was touched"


async def test_unchecked_suggestions_on_the_original_are_rejected_once_the_halves_exist(h: Harness) -> None:
    assert [f["status"] for f in facts_on(h, SPREAD)] == ["suggested"]
    sent = await h.client.post(split_path(h, page(h, SPREAD)["id"]), json=CUT)
    assert sent.status_code == 202
    await h.drain()
    assert [f["status"] for f in facts_on(h, SPREAD)] == ["rejected"]


async def test_each_half_has_its_own_words_and_starts_unread(h: Harness) -> None:
    await h.client.post(split_path(h, page(h, SPREAD)["id"]), json=CUT)
    await h.drain()
    ids = {n: page(h, n)["id"] for n in (SPREAD, len(BUNDLE) + 1, len(BUNDLE) + 2)}
    for n, page_id in ids.items():
        row = h.admin.execute("select count(*) from app.ocr_words where page_id = %s", (page_id,)).fetchone()
        assert row is not None and row[0] > 0, f"page {n} has no words of its own"
    for n in (len(BUNDLE) + 1, len(BUNDLE) + 2):
        blind = await h.client.get(f"/v1/cases/{h.case_id}/pages/{n}/facts")
        assert blind.status_code == 423, "a half showed suggestions before it was read"


async def test_a_page_is_split_once_and_bad_cuts_are_refused(h: Harness) -> None:
    page_id = page(h, SPREAD)["id"]
    assert (await h.client.post(split_path(h, page_id), json=CUT)).status_code == 202
    again = await h.client.post(split_path(h, page_id), json=CUT)
    assert again.status_code == 409, "a split page was split again"
    assert (await h.client.post(split_path(h, uuid.uuid4()), json=CUT)).status_code == 404
    for bad in ({**CUT, "at": 0.0}, {**CUT, "at": 1.0}, {**CUT, "axis": "diagonal"}, {**CUT, "first": "top"}):
        refused = await h.client.post(split_path(h, page(h, 1)["id"]), json=bad)
        assert refused.status_code == 422, bad


async def test_a_page_set_aside_cannot_be_split_and_a_half_can(h: Harness) -> None:
    h.admin.execute(
        "update app.pages set superseded_by_page_id = %s where id = %s", (page(h, 3)["id"], page(h, 1)["id"])
    )
    assert (await h.client.post(split_path(h, page(h, 1)["id"]), json=CUT)).status_code == 409
    assert (await h.client.post(split_path(h, page(h, SPREAD)["id"]), json=CUT)).status_code == 202
    await h.drain()
    half = page(h, len(BUNDLE) + 1)
    assert (
        await h.client.post(split_path(h, half["id"]), json={**CUT, "axis": "horizontal", "first": "top"})
    ).status_code == 202


async def test_who_may_split(h: Harness, seed: Seed, client_for: ClientFor) -> None:
    viewer, assistant = seed.person(), seed.person()
    seed.case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    seed.case_member(h.case_id, assistant, "assistant", added_by=h.expert)
    path = split_path(h, page(h, SPREAD)["id"])
    assert (await (await client_for(seed.person())).post(path, json=CUT)).status_code == 404
    assert (await (await client_for(viewer)).post(path, json=CUT)).status_code == 403
    # client_for apps have no file storage (503 once access is allowed), so the assistant is shown to pass
    # the access check by reaching the 409 for a page the expert has already split.
    assert (await h.client.post(path, json=CUT)).status_code == 202
    assert (await (await client_for(assistant)).post(path, json=CUT)).status_code == 409


async def test_the_bundle_summary_counts_splits(h: Harness) -> None:
    summary = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert summary.json()["splits"] == 0
    await h.client.post(split_path(h, page(h, SPREAD)["id"]), json=CUT)
    await h.drain()
    assert (await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")).json()["splits"] == 1
