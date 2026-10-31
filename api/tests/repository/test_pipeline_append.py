"""Slice A increment 9: later records (architecture 5.2 08b, 7; brief "Appended records").

- A file uploaded with `append` is numbered after the case's last page and only its pages are read
  and sent to the model; earlier pages keep their numbers and are not read again.
- Each delivery of later records is a new bundle version: files appended before they are processed
  share one version, a later append gets the next.
- `POST /v1/cases/{id}/documents/append` (case.upload) groups appended files into one new version
  and starts processing their pages (202 + job). Files already cut into pages cannot be regrouped.
- Slow (RUN_SLOW=1): appending the 14-page `appended-records.pdf` to the 900-page bundle numbers its
  pages 901-914 and processes only those.
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import (
    BUNDLE,
    Harness,
    empty_answer,
    expected,
    harness,
    recorded_book,
    t07b_requests,
    text_pdf,
)
from tests.support.ai import ScriptedBedrock
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

FIRST = [["Synthetic first one"], ["Synthetic first two"], ["Synthetic first three"]]
LATER = [["Synthetic later one"], ["Synthetic later two"]]
MORE = [["Synthetic more one"]]


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
    first_source = default_library().combine(*combo.codes).source_types()[0]["key"]
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([empty_answer(first_source)]),
    ) as running:
        yield running


def versions(h: Harness) -> dict[uuid.UUID, int]:
    return dict(
        h.admin.execute("select id, bundle_version from app.documents where case_id = %s", (h.case_id,)).fetchall()
    )


async def test_appended_pages_follow_the_last_page_and_only_they_are_processed(h: Harness) -> None:
    first = await h.upload(text_pdf(FIRST))
    await h.drain()
    before = {p["id"]: p["bundle_page_no"] for p in h.pages()}
    asked = len(t07b_requests(h))
    assert asked == 3
    later = await h.upload(text_pdf(LATER), filename="later.pdf", append=True)
    assert set((await h.drain()).values()) == {"done"}
    pages = h.pages()
    assert [p["bundle_page_no"] for p in pages] == [1, 2, 3, 4, 5]
    assert {p["id"]: p["bundle_page_no"] for p in pages if p["id"] in before} == before
    assert {p["bundle_page_no"] for p in pages if p["document_id"] == later} == {4, 5}
    assert len(t07b_requests(h)) == asked + 2
    assert versions(h) == {first: 1, later: 2}


async def test_files_appended_together_share_a_version_and_a_later_append_gets_the_next(h: Harness) -> None:
    first = await h.upload(text_pdf(FIRST))
    await h.drain()
    a = await h.upload(text_pdf(LATER), filename="a.pdf", append=True)
    b = await h.upload(text_pdf(MORE), filename="b.pdf", append=True)
    await h.drain()
    c = await h.upload(text_pdf(MORE), filename="c.pdf", append=True)
    await h.drain()
    assert versions(h) == {first: 1, a: 2, b: 2, c: 3}
    got = await h.client.get(f"/v1/cases/{h.case_id}/bundle/versions")
    assert [(v["bundle_version"], v["documents"], v["pages"]) for v in got.json()["versions"]] == [
        (1, 1, 3),
        (2, 2, 3),
        (3, 1, 1),
    ]


async def test_the_append_route_groups_files_into_one_new_version_and_starts_processing(
    h: Harness, seed: Seed, client_for: ClientFor
) -> None:
    first = await h.upload(text_pdf(FIRST))
    await h.drain()
    a = await h.upload(text_pdf(LATER), filename="a.pdf", append=True)
    h.admin.execute("update app.documents set bundle_version = 7 where id = %s", (a,))  # as if apart
    b = await h.upload(text_pdf(MORE), filename="b.pdf", append=True)
    path = f"/v1/cases/{h.case_id}/documents/append"
    sent = await h.client.post(path, json={"document_ids": [str(a), str(b)]})
    assert sent.status_code == 202, sent.status_code
    assert sent.json()["bundle_version"] == 2
    job = uuid.UUID(sent.json()["job_id"])
    assert h.admin.execute("select kind from app.jobs where id = %s", (job,)).fetchone() == ("process_pages",)
    assert versions(h) == {first: 1, a: 2, b: 2}
    assert set((await h.drain()).values()) == {"done"}
    assert [p["bundle_page_no"] for p in h.pages()] == [1, 2, 3, 4, 5, 6]

    again = await h.client.post(path, json={"document_ids": [str(a)]})
    assert again.status_code == 409, again.status_code
    assert (await h.client.post(path, json={"document_ids": [str(uuid.uuid4())]})).status_code == 404
    assert (await h.client.post(path, json={"document_ids": []})).status_code == 422

    viewer = seed.person()
    seed.case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    refused = await (await client_for(viewer)).post(path, json={"document_ids": [str(b)]})
    assert refused.status_code == 403
    stranger = await client_for(seed.person())
    assert (await stranger.post(path, json={"document_ids": [str(b)]})).status_code == 404


@pytest.mark.slow
@pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="the 900-page bundle runs with RUN_SLOW=1")
async def test_fourteen_appended_pages_become_901_to_914_and_only_they_are_processed(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    store = FixtureStore(BUNDLE / "recordings")
    case = expected("case.json")
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=store,
        bedrock=recorded_book(store),
        names=case["case_names"],
        dates_of_birth=[dt.date.fromisoformat(case["dob"])],
    ) as h:
        await h.upload((BUNDLE / "records.pdf").read_bytes())
        assert set((await h.drain(timeout=3600)).values()) == {"done"}
        assert h.ocr is not None
        ocr_before, model_before = len(h.ocr.sent), len(t07b_requests(h))
        later = await h.upload((BUNDLE / "appended-records.pdf").read_bytes(), filename="later.pdf", append=True)
        assert set((await h.drain(timeout=600)).values()) == {"done"}
        new = [p for p in h.pages() if p["document_id"] == later]
        assert sorted(p["bundle_page_no"] for p in new) == list(range(901, 915))
        assert sorted(int(p["page"]) for p in expected("appended.json")) == list(range(901, 915))
        scanned = sum(1 for p in new if p["text_source"] == "ocr")
        assert len(h.ocr.sent) - ocr_before == scanned
        assert len(t07b_requests(h)) - model_before >= 14
        assert {p["bundle_page_no"] for p in h.pages()} == set(range(1, 915))
        assert versions(h)[later] == 2
