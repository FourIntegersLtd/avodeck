"""Slice A increment 11c-1: a clearer copy of a page (S6, architecture 10.3 "Clearer copies").

- The clearer copy is uploaded as later records (`/uploads` with `append`), so its page gets the next
  number. `POST /v1/cases/{id}/pages/{page_id}/replace` `{document_id}` (page.replace) says which page
  it replaces: once the document has been split, the old page is superseded by the document's first
  page (`pages.superseded_by_page_id`), whether the request comes before or after the split.
- The old page keeps its number, stays readable and keeps its facts: nothing moves automatically.
  New suggestions are read from the new page. Audited.
- A page is replaced once (409 after); a document replaces one page (409); unknown pages or
  documents, and documents of another case, are 404. Viewers get 403, people outside the case 404.
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
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_quarantine import answer, audited
from tests.repository.test_pipeline_support import Harness, facts_of, harness, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.db import ClientFor, scope_for
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

FAINT = 2
BUNDLE = [["Synthetic alpha entry"], ["Synthetic bravo entry"], ["Synthetic charlie entry"]]
CLEARER = [["Synthetic bravo entry clearer"]]


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
    """Three pages read; the fact on page 2 is verified."""
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
        await running.upload(text_pdf(BUNDLE))
        await running.drain()
        (fact,) = facts_on(running, FAINT)
        admin.execute("update app.facts set status = 'verified' where id = %s", (fact["id"],))
        yield running


def page(h: Harness, n: int) -> dict[str, Any]:
    return next(p for p in h.pages() if p["bundle_page_no"] == n)


def facts_on(h: Harness, n: int) -> list[dict[str, Any]]:
    return [f for f in facts_of(h) if f["bundle_page_no"] == n]


def replace_path(h: Harness, page_id: uuid.UUID) -> str:
    return f"/v1/cases/{h.case_id}/pages/{page_id}/replace"


async def test_a_clearer_copy_gets_the_next_number_and_supersedes_the_old_page(h: Harness) -> None:
    old = page(h, FAINT)
    (kept,) = facts_on(h, FAINT)
    document_id = await h.upload(text_pdf(CLEARER), append=True)

    sent = await h.client.post(replace_path(h, old["id"]), json={"document_id": str(document_id)})
    assert sent.status_code == 200, sent.status_code
    assert sent.json()["page_id"] == str(old["id"]) and sent.json()["new_page_no"] is None
    await h.drain()

    new = page(h, len(BUNDLE) + 1)
    assert new["document_id"] == document_id
    assert page(h, FAINT)["superseded_by_page_id"] == new["id"]
    assert page(h, FAINT)["bundle_page_no"] == FAINT
    thumb = await h.client.get(f"/v1/cases/{h.case_id}/pages/{FAINT}/thumbnail")
    assert thumb.status_code == 200, "the superseded page is no longer readable"

    (still,) = facts_on(h, FAINT)
    assert (still["id"], still["status"]) == (kept["id"], "verified"), "a fact moved or changed by itself"
    assert [f["status"] for f in facts_on(h, new["bundle_page_no"])] == ["suggested"]
    assert audited(h, "page.replaced") == 1


async def test_the_link_is_made_when_the_copy_was_read_first(h: Harness) -> None:
    old = page(h, FAINT)
    document_id = await h.upload(text_pdf(CLEARER), append=True)
    await h.drain()
    assert page(h, FAINT)["superseded_by_page_id"] is None, "a page was superseded without being asked"

    sent = await h.client.post(replace_path(h, old["id"]), json={"document_id": str(document_id)})
    assert sent.status_code == 200, sent.status_code
    assert sent.json()["new_page_no"] == len(BUNDLE) + 1
    assert page(h, FAINT)["superseded_by_page_id"] == page(h, len(BUNDLE) + 1)["id"]


async def test_a_page_is_replaced_once_and_a_copy_replaces_one_page(
    h: Harness, seed: Seed, client_for: ClientFor
) -> None:
    first = await h.upload(text_pdf(CLEARER), append=True)
    second = await h.upload(text_pdf(CLEARER), append=True)
    await h.drain()
    ok = await h.client.post(replace_path(h, page(h, FAINT)["id"]), json={"document_id": str(first)})
    assert ok.status_code == 200
    again = await h.client.post(replace_path(h, page(h, FAINT)["id"]), json={"document_id": str(second)})
    assert again.status_code == 409, "a superseded page was replaced again"
    reused = await h.client.post(replace_path(h, page(h, 1)["id"]), json={"document_id": str(first)})
    assert reused.status_code == 409, "one copy replaced two pages"

    assert (await h.client.post(replace_path(h, uuid.uuid4()), json={"document_id": str(second)})).status_code == 404
    unknown = await h.client.post(replace_path(h, page(h, 1)["id"]), json={"document_id": str(uuid.uuid4())})
    assert unknown.status_code == 404
    elsewhere = seed.case(seed.org(members={h.expert: "owner"}), h.expert)
    (foreign,) = h.admin.execute(
        "insert into app.documents (case_id, kind, filename, sha256)"
        " values (%s, 'records', 'other.pdf', repeat('0', 64)) returning id",
        (elsewhere,),
    ).fetchone()  # type: ignore[misc]
    crossed = await h.client.post(replace_path(h, page(h, 1)["id"]), json={"document_id": str(foreign)})
    assert crossed.status_code == 404, "a document of another case was used"

    viewer, assistant = seed.person(), seed.person()
    seed.case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    seed.case_member(h.case_id, assistant, "assistant", added_by=h.expert)
    body = {"document_id": str(second)}
    outsider = await client_for(seed.person())
    assert (await outsider.post(replace_path(h, page(h, 1)["id"]), json=body)).status_code == 404
    assert (await (await client_for(viewer)).post(replace_path(h, page(h, 1)["id"]), json=body)).status_code == 403
    helped = await (await client_for(assistant)).post(replace_path(h, page(h, 1)["id"]), json=body)
    assert helped.status_code == 200, helped.status_code


async def test_replacements_are_kept_per_case_for_people_who_replace_pages(h: Harness, seed: Seed) -> None:
    viewer, stranger = seed.person(), seed.person()
    seed.case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    document_id = await h.upload(text_pdf(CLEARER), append=True)
    insert = "insert into app.page_replacements (case_id, page_id, document_id, requested_by) values (%s, %s, %s, %s)"
    for person in (viewer, stranger):
        async with h.api_db.request(scope_for(person)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(insert, (h.case_id, page(h, 1)["id"], document_id, person))
    async with h.api_db.request(scope_for(h.expert)) as conn:
        await conn.execute(insert, (h.case_id, page(h, 1)["id"], document_id, h.expert))
    async with h.api_db.request(scope_for(viewer)) as conn:
        cur = await conn.execute("select count(*) from app.page_replacements where case_id = %s", (h.case_id,))
        assert await cur.fetchone() == (1,)
    async with h.api_db.request(scope_for(stranger)) as conn:
        cur = await conn.execute("select count(*) from app.page_replacements where case_id = %s", (h.case_id,))
        assert await cur.fetchone() == (0,)
    async with h.api_db.request(scope_for(h.expert)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("delete from app.page_replacements where case_id = %s", (h.case_id,))
