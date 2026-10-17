"""Slice A increment 6: the missing documents list (architecture 5.2, state-machines "Missing item").

- `GET /v1/cases/{id}/missing-items`: each document a page mentions that the bundle does not hold,
  with the page that mentions it, its status and version.
- `PATCH /v1/cases/{id}/missing-items/{item_id}` with If-Match, `{"status": ...}`:
  `not_available` (a reason is required) from open or requested; `open` again from not_available;
  `received` from requested, naming a page added to the case after the item was listed. Any other
  change is 409; `requested` is set only by sending a request (08b), never here.

The named expert and the assistant may change items; a viewer may look; anyone without access to
the case gets 404. Responses and errors carry no page text beyond the item's own description.
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
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import Harness, harness, text_pdf
from tests.support.ai import ScriptedBedrock, response
from tests.support.auth import CSRF_HEADER, cookie_header
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LETTER = [
    "Practice letter about the review appointment on 03/04/2024",
    "Colonoscopy report to follow from the hospital team",
]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
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
    storage: SupabaseStorage,
) -> AsyncIterator[Harness]:
    """One typed letter mentioning a report the model finds on no page."""
    pack = default_library().combine(*LAUNCH.codes)
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    read = response(
        {
            "source_type": pack.source_types()[0]["key"],
            "printed_dates": [],
            "facts": [],
            "references_out": [
                {"described_document": "Colonoscopy report", "date": None, "word_ids": ["p1w9", "p1w10"]}
            ],
            "availability_evidence": [],
            "actions_requested_of_clinician": [],
        }
    )
    unmatched = response({"reference_id": "p1-r1", "matched_page": None, "confidence": 0.9})
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([read, unmatched]),
    ) as h:
        await h.upload(text_pdf([LETTER]))
        assert set((await h.drain()).values()) == {"done"}
        yield h


def as_person(h: Harness, person: uuid.UUID) -> dict[str, str]:
    session = Seed(h.admin).session(person)
    return {"cookie": cookie_header(session), CSRF_HEADER: session.csrf}


def member(h: Harness, role: str) -> dict[str, str]:
    person = Seed(h.admin).person()
    Seed(h.admin).case_member(h.case_id, person, role, added_by=h.expert)
    return as_person(h, person)


def audited(h: Harness) -> list[str]:
    return [r[0] for r in h.admin.execute("select action from app.audit_log where case_id = %s", (h.case_id,))]


def item_id(h: Harness) -> uuid.UUID:
    (row,) = h.admin.execute("select id from app.missing_items where case_id = %s", (h.case_id,)).fetchall()
    return uuid.UUID(str(row[0]))


def set_requested(h: Harness) -> None:
    h.admin.execute("update app.missing_items set status = 'requested' where case_id = %s", (h.case_id,))


def later_page(h: Harness) -> uuid.UUID:
    """A page added to the case after the item was listed (records sent in answer to a request)."""
    doc = Seed(h.admin).document(h.case_id)
    return uuid.UUID(
        str(
            h.admin.execute(
                "insert into app.pages (case_id, document_id, bundle_page_no) values (%s, %s, 2) returning id",
                (h.case_id, doc),
            ).fetchone()[0]
        )
    )


async def listed(h: Harness, **kwargs: Any) -> httpx.Response:
    return await h.client.get(f"/v1/cases/{h.case_id}/missing-items", **kwargs)


async def change(
    h: Harness, item: uuid.UUID, body: dict[str, Any], version: int | None, **kwargs: Any
) -> httpx.Response:
    headers = dict(kwargs.pop("headers", {}))
    if version is not None:
        headers["if-match"] = etag(version)
    return await h.client.patch(f"/v1/cases/{h.case_id}/missing-items/{item}", json=body, headers=headers, **kwargs)


async def test_the_missing_document_is_listed_with_the_page_that_mentions_it(bundle: Harness) -> None:
    got = await listed(bundle)
    assert got.status_code == 200, got.status_code
    (item,) = got.json()["missing_items"]
    page = bundle.pages()[0]
    assert item["id"] == str(item_id(bundle))
    assert (item["citing_page_id"], item["citing_page_no"]) == (str(page["id"]), 1)
    assert item["word_ids"] == ["p1w9", "p1w10"]
    same = item["description"] == "Colonoscopy report"
    assert same, "page 1: the description is not the document named on the page"
    assert item["status"] == "open" and item["reason"] is None and item["version"] == 0
    assert item["received_page_id"] is None and item["received_page_no"] is None
    assert got.headers["cache-control"] == "private, no-store"


async def test_not_available_needs_a_reason_and_can_be_opened_again(bundle: Harness) -> None:
    item = item_id(bundle)
    assert (await change(bundle, item, {"status": "not_available"}, 0)).status_code == 422
    done = await change(bundle, item, {"status": "not_available", "reason": "Destroyed under the practice policy"}, 0)
    assert done.status_code == 200, done.status_code
    assert done.headers["etag"] == etag(1)
    assert done.json()["status"] == "not_available" and done.json()["version"] == 1
    same = done.json()["reason"] == "Destroyed under the practice policy"
    assert same, "the reason given was not kept"
    sealed = bundle.admin.execute("select reason from app.missing_items where id = %s", (item,)).fetchone()[0]
    assert b"Destroyed" not in bytes(sealed), "the reason is stored unsealed"
    again = await change(bundle, item, {"status": "open"}, 1)
    assert again.status_code == 200, again.status_code
    assert again.json()["status"] == "open" and again.json()["reason"] is None
    assert {"missing_item.not_available", "missing_item.reopened"} <= set(audited(bundle))


async def test_received_needs_a_request_and_a_page_added_later(bundle: Harness) -> None:
    item = item_id(bundle)
    page = later_page(bundle)
    assert (await change(bundle, item, {"status": "received", "page_id": str(page)}, 0)).status_code == 409
    set_requested(bundle)
    assert (await change(bundle, item, {"status": "received"}, 0)).status_code == 422
    first = bundle.pages()[0]["id"]
    assert (await change(bundle, item, {"status": "received", "page_id": str(first)}, 0)).status_code == 409
    assert (await change(bundle, item, {"status": "received", "page_id": str(uuid.uuid4())}, 0)).status_code == 409
    done = await change(bundle, item, {"status": "received", "page_id": str(page)}, 0)
    assert done.status_code == 200, done.status_code
    assert done.json()["status"] == "received"
    assert (done.json()["received_page_id"], done.json()["received_page_no"]) == (str(page), 2)
    assert "missing_item.received" in audited(bundle)
    for body in ({"status": "open"}, {"status": "not_available", "reason": "Lost"}):
        assert (await change(bundle, item, body, 1)).status_code == 409, body


async def test_requested_is_never_set_here_and_unknown_items_are_404(bundle: Harness) -> None:
    item = item_id(bundle)
    assert (await change(bundle, item, {"status": "requested"}, 0)).status_code == 422
    assert (await change(bundle, uuid.uuid4(), {"status": "open"}, 0)).status_code == 404
    assert (await change(bundle, item, {"status": "open"}, 0)).status_code == 409


async def test_a_change_needs_the_etag_last_read(bundle: Harness) -> None:
    item = item_id(bundle)
    body = {"status": "not_available", "reason": "Lost"}
    assert (await change(bundle, item, body, None)).status_code == 428
    assert (await change(bundle, item, body, 4)).status_code == 412
    assert (await listed(bundle)).json()["missing_items"][0]["status"] == "open"


async def test_the_assistant_may_change_a_viewer_may_only_look(bundle: Harness) -> None:
    item = item_id(bundle)
    viewer = member(bundle, "viewer")
    assert (await listed(bundle, headers=viewer)).status_code == 200
    got = await change(bundle, item, {"status": "not_available", "reason": "Lost"}, 0, headers=viewer)
    assert got.status_code == 403, got.status_code
    assistant = member(bundle, "assistant")
    got = await change(bundle, item, {"status": "not_available", "reason": "Lost"}, 0, headers=assistant)
    assert got.status_code == 200, got.status_code


async def test_anyone_without_access_to_the_case_gets_404(bundle: Harness) -> None:
    other = as_person(bundle, Seed(bundle.admin).person())
    assert (await listed(bundle, headers=other)).status_code == 404
    got = await change(bundle, item_id(bundle), {"status": "not_available", "reason": "Lost"}, 0, headers=other)
    assert got.status_code == 404, got.status_code
    assert (await listed(bundle)).json()["missing_items"][0]["status"] == "open"
