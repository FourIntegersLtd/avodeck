"""Slice A increment 5: reviewing copies (architecture 5.2, F13; screen S07).

- `GET /v1/cases/{id}/duplicates`: each page set aside, the page it copies and its review state.
- `PATCH /v1/cases/{id}/pages/{page_id}/duplicate` `{"action": "confirm" | "undo"}` with If-Match:
  confirm keeps it set aside; undo restores the page with everything found on it (F13), and it is
  never set aside again.
- `GET /v1/cases/{id}/pages/{page_no}/thumbnail`: the page's small picture, never cached.

Anyone without access to the case gets 404; a viewer may look but not change. Responses carry page
numbers and ids only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest

from app.core.db import Database
from app.core.http import etag
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import Harness, empty_answer, harness, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.auth import CSRF_HEADER, cookie_header
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LETTER = [
    "Practice letter about the review appointment on 03/04/2024",
    "Blood pressure 128/82 and weight 71 kg were recorded",
    "Please book a further review in six weeks with the team",
]
OTHER_VALUE = [LETTER[0], "Blood pressure 138/82 and weight 71 kg were recorded", LETTER[2]]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def bundle(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    """Three typed pages; page 2 is a copy of page 1, page 3 has another value."""
    pack = default_library().combine(*LAUNCH.codes)
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([empty_answer(pack.source_types()[0]["key"])]),
    ) as h:
        await h.upload(text_pdf([LETTER, LETTER, OTHER_VALUE]))
        assert set((await h.drain()).values()) == {"done"}
        yield h


def pages_of(h: Harness) -> dict[int, dict[str, Any]]:
    return {p["bundle_page_no"]: p for p in h.pages()}


def as_person(h: Harness, person: uuid.UUID) -> dict[str, str]:
    session = Seed(h.admin).session(person)
    return {"cookie": cookie_header(session), CSRF_HEADER: session.csrf}


def member(h: Harness, role: str) -> dict[str, str]:
    person = Seed(h.admin).person()
    Seed(h.admin).case_member(h.case_id, person, role, added_by=h.expert)
    return as_person(h, person)


def stranger(h: Harness) -> dict[str, str]:
    return as_person(h, Seed(h.admin).person())


def audited(h: Harness) -> list[str]:
    return [r[0] for r in h.admin.execute("select action from app.audit_log where case_id = %s", (h.case_id,))]


async def listed(h: Harness, **kwargs: Any) -> httpx.Response:
    return await h.client.get(f"/v1/cases/{h.case_id}/duplicates", **kwargs)


async def review(h: Harness, page_id: uuid.UUID, action: str, version: int | None, **kwargs: Any) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}))
    if version is not None:
        headers["if-match"] = etag(version)
    return await h.client.patch(
        f"/v1/cases/{h.case_id}/pages/{page_id}/duplicate", json={"action": action}, headers=headers, **kwargs
    )


async def test_the_copies_are_listed_with_the_page_they_copy(bundle: Harness) -> None:
    pages = pages_of(bundle)
    got = await listed(bundle)
    assert got.status_code == 200, got.status_code
    assert got.json() == {
        "duplicates": [
            {
                "page_id": str(pages[2]["id"]),
                "page_no": 2,
                "copy_of_page_id": str(pages[1]["id"]),
                "copy_of_page_no": 1,
                "status": "suggested",
                "version": 0,
            }
        ]
    }


async def test_confirming_keeps_the_copy_set_aside_and_moves_the_etag_on(bundle: Harness) -> None:
    copy = pages_of(bundle)[2]
    done = await review(bundle, copy["id"], "confirm", 0)
    assert done.status_code == 200, done.status_code
    assert done.headers["etag"] == etag(1)
    assert done.json()["status"] == "confirmed" and done.json()["copy_of_page_no"] == 1
    assert pages_of(bundle)[2]["duplicate_of_page_id"] == pages_of(bundle)[1]["id"]
    assert (await listed(bundle)).json()["duplicates"][0]["status"] == "confirmed"
    actions = [
        r[0] for r in bundle.admin.execute("select action from app.audit_log where case_id = %s", (bundle.case_id,))
    ]
    assert "page.duplicate_confirmed" in actions


async def test_a_change_needs_the_etag_last_read(bundle: Harness) -> None:
    copy = pages_of(bundle)[2]
    assert (await review(bundle, copy["id"], "confirm", None)).status_code == 428
    assert (await review(bundle, copy["id"], "confirm", 5)).status_code == 412
    assert pages_of(bundle)[2]["duplicate_of_page_id"] is not None


async def test_undo_restores_the_page_and_it_is_never_set_aside_again(bundle: Harness) -> None:
    pages = pages_of(bundle)
    facts = bundle.admin.execute("select count(*) from app.facts where page_id = %s", (pages[2]["id"],)).fetchone()
    done = await review(bundle, pages[2]["id"], "undo", 0)
    assert done.status_code == 200, done.status_code
    assert done.headers["etag"] == etag(1)
    assert done.json()["status"] == "kept" and done.json()["copy_of_page_id"] is None
    assert pages_of(bundle)[2]["duplicate_of_page_id"] is None
    assert (await listed(bundle)).json() == {"duplicates": []}
    after = bundle.admin.execute("select count(*) from app.facts where page_id = %s", (pages[2]["id"],)).fetchone()
    assert after == facts, "undo changed the page's facts"
    await bundle.enqueue("process_pages")
    assert set((await bundle.drain()).values()) == {"done"}
    assert pages_of(bundle)[2]["duplicate_of_page_id"] is None, "a restored page was set aside again"
    actions = [
        r[0] for r in bundle.admin.execute("select action from app.audit_log where case_id = %s", (bundle.case_id,))
    ]
    assert "page.duplicate_undone" in actions


async def test_only_a_page_set_aside_can_be_confirmed_or_undone(bundle: Harness) -> None:
    pages = pages_of(bundle)
    for action in ("confirm", "undo"):
        got = await review(bundle, pages[3]["id"], action, 0)
        assert got.status_code == 409, (action, got.status_code)
    assert (await review(bundle, pages[2]["id"], "delete", 0)).status_code == 422
    assert (await review(bundle, uuid.uuid4(), "confirm", 0)).status_code == 404


async def test_the_thumbnail_is_streamed_and_never_cached(bundle: Harness) -> None:
    got = await bundle.client.get(f"/v1/cases/{bundle.case_id}/pages/2/thumbnail")
    assert got.status_code == 200, got.status_code
    assert got.headers["content-type"] == "image/jpeg"
    assert got.headers["cache-control"] == "private, no-store"
    assert got.content[:3] == b"\xff\xd8\xff"
    assert (await bundle.client.get(f"/v1/cases/{bundle.case_id}/pages/99/thumbnail")).status_code == 404


async def test_a_viewer_may_look_but_not_change(bundle: Harness) -> None:
    viewer = member(bundle, "viewer")
    assert (await listed(bundle, headers=viewer)).status_code == 200
    assert (await bundle.client.get(f"/v1/cases/{bundle.case_id}/pages/1/thumbnail", headers=viewer)).status_code == 200
    got = await review(bundle, pages_of(bundle)[2]["id"], "undo", 0, headers=viewer)
    assert got.status_code == 403, got.status_code
    assert pages_of(bundle)[2]["duplicate_of_page_id"] is not None


async def test_anyone_without_access_to_the_case_gets_404(bundle: Harness) -> None:
    other = stranger(bundle)
    assert (await listed(bundle, headers=other)).status_code == 404
    assert (await bundle.client.get(f"/v1/cases/{bundle.case_id}/pages/1/thumbnail", headers=other)).status_code == 404
    got = await review(bundle, pages_of(bundle)[2]["id"], "undo", 0, headers=other)
    assert got.status_code == 404, got.status_code
    assert pages_of(bundle)[2]["duplicate_of_page_id"] is not None


async def put_aside(h: Harness, page_id: uuid.UUID | str, copy_of: int, **kwargs: Any) -> httpx.Response:
    return await h.client.put(
        f"/v1/cases/{h.case_id}/pages/{page_id}/duplicate", json={"copy_of_page_no": copy_of}, **kwargs
    )


async def test_the_expert_sets_a_page_aside_as_a_copy_of_another(bundle: Harness) -> None:
    pages = pages_of(bundle)
    done = await put_aside(bundle, pages[3]["id"], 1)
    assert done.status_code == 200, done.status_code
    body = done.json()
    assert body["status"] == "confirmed" and body["copy_of_page_no"] == 1 and body["page_no"] == 3
    assert done.headers["etag"] == etag(body["version"]) and body["version"] >= 1
    assert pages_of(bundle)[3]["duplicate_of_page_id"] == pages[1]["id"]
    assert "page.duplicate_set_aside" in audited(bundle)


async def test_a_page_cannot_be_its_own_copy_or_copy_a_copy(bundle: Harness) -> None:
    pages = pages_of(bundle)
    assert (await put_aside(bundle, pages[3]["id"], 3)).status_code == 422
    assert (await put_aside(bundle, pages[3]["id"], 2)).status_code == 409
    assert pages_of(bundle)[3]["duplicate_of_page_id"] is None


async def test_setting_aside_needs_pages_in_the_case_and_the_right_role(bundle: Harness) -> None:
    pages = pages_of(bundle)
    assert (await put_aside(bundle, uuid.uuid4(), 1)).status_code == 404
    assert (await put_aside(bundle, pages[3]["id"], 99)).status_code == 404
    assert (await put_aside(bundle, pages[3]["id"], 1, headers=stranger(bundle))).status_code == 404
    assert (await put_aside(bundle, pages[3]["id"], 1, headers=member(bundle, "viewer"))).status_code == 403
