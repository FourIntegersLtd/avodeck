"""Slice A increment 6: documents mentioned but not in the bundle (T-07c) and quiet record sources.

After copies are set aside (the brief's order), the pipeline works out where each record source goes
quiet (`record_gaps`, from the pages' dates and kinds, by code) and asks the model, once for each
document a page mentions (`references_out`), which page of the case holds it (T-07c). The model is
shown only the reference's own words and a list of the case's pages made by code (number, source,
dates, kinds), with identifiers stripped (rule 7). A reference with no page, or a page the case does
not have, becomes a missing item (`missing_items`, status `open`) citing its page and words; its
description is the model's words only if they are on the page (rule 2), else the cited words, and it
is sealed. A rerun asks nothing again. Messages name pages only (rule 6).
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.pipeline import paths
from tests.repository.test_pipeline_support import (
    Harness,
    empty_answer,
    expected,
    harness,
    identifier_leaked,
    manifest,
    recorded_book,
    rekeyed_store,
    subset_document,
    subset_numbers,
    subset_page_bytes,
    text_pdf,
)
from tests.support.ai import CELL_ENV, response
from tests.support.db import scope_for
from tests.support.files import local_storage
from tests.support.packs import BOTH, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LETTER = [
    "Practice letter about the review appointment on 03/04/2024",
    "Colonoscopy report to follow from the hospital team",
    "Ultrasound scan result is on the next page",
]
SCAN = ["Ultrasound scan report from the imaging department", "No abnormality was seen on 05/04/2024"]
COLONOSCOPY_WORDS = ["p1w9", "p1w10"]
ULTRASOUND_WORDS = ["p1w17", "p1w18"]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def subset(
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    recordings = rekeyed_store(tmp_path / "recordings", subset_document(), subset_page_bytes())
    case = expected("case.json")
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
        ocr_store=recordings,
        bedrock=recorded_book(recordings),
        names=list(case["case_names"]),
        dates_of_birth=[dt.date.fromisoformat(case["dob"])],
    ) as h:
        await h.upload(subset_document())
        finished = await h.drain(timeout=300)
        assert set(finished.values()) == {"done"}, finished
        yield h


def items_of(h: Harness) -> list[dict[str, Any]]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    return cur.execute(
        "select m.*, p.bundle_page_no from app.missing_items m join app.pages p on p.id = m.citing_page_id"
        " where m.case_id = %s order by p.bundle_page_no, m.reference_key",
        (h.case_id,),
    ).fetchall()


def t07c_requests(h: Harness) -> list[ModelRequest]:
    return [r for r in h.bedrock.requests if r.task_id == "T-07c"]


async def description(h: Harness, item: dict[str, Any]) -> str:
    return (await h.decrypt(bytes(item["description"]), paths.missing_description_purpose(item["id"]))).decode()


async def test_the_report_mentioned_on_page_390_is_listed_as_missing(subset: Harness) -> None:
    numbers = dict(enumerate(subset_numbers(), start=1))
    (planted,) = expected("missing.json")
    (cited,) = [r for r in manifest()["references"] if r["page"] == planted["page"] and r["matched_page"] is None]
    found = [i for i in items_of(subset) if numbers[i["bundle_page_no"]] == planted["page"]]
    match = [i for i in found if list(i["word_ids"]) == list(cited["word_ids"])]
    assert len(match) == 1, f"page {planted['page']}: {len(match)} missing items for the planted reference"
    item = match[0]
    same = await description(subset, item) == planted["described_document"]
    assert same, f"page {planted['page']}: the description is not the document named on the page"
    assert item["status"] == "open" and item["version"] == 0
    assert item["reference_date"] == dt.date.fromisoformat(planted["date"])


async def test_each_reference_is_asked_once_with_identifiers_stripped(subset: Harness) -> None:
    numbers = set(subset_numbers())
    references = [r for r in manifest()["references"] if r["page"] in numbers]
    sent = t07c_requests(subset)
    assert len(sent) == len(references) == len({r.subject for r in sent})
    recognise = default_library().combine(*LAUNCH.codes).identifier_recognisers()
    texts = [r.text for r in sent] + [r.system for r in sent]
    assert not identifier_leaked(texts, expected("case.json"), recognise), "an identifier reached the model"


async def test_a_matched_page_the_case_does_not_have_is_still_missing(subset: Harness) -> None:
    """The subset keeps the bundle's references but not the pages they match, so every one is missing."""
    numbers = dict(enumerate(subset_numbers(), start=1))
    kept = set(numbers.values())
    wanted = sorted(int(r["page"]) for r in manifest()["references"] if r["page"] in kept)
    assert sorted(numbers[i["bundle_page_no"]] for i in items_of(subset)) == wanted


async def test_running_again_asks_nothing_and_changes_nothing(subset: Harness) -> None:
    before = [(i["id"], i["status"], i["version"]) for i in items_of(subset)]
    asked = len(subset.bedrock.requests)
    await subset.enqueue("process_pages")
    assert set((await subset.drain()).values()) == {"done"}
    assert [(i["id"], i["status"], i["version"]) for i in items_of(subset)] == before
    assert len(subset.bedrock.requests) == asked


async def test_a_few_pages_make_no_record_gap_and_jobs_hold_only_step_codes(subset: Harness) -> None:
    from app.features.pipeline.service import STEPS

    gaps = subset.admin.execute("select count(*) from app.record_gaps where case_id = %s", (subset.case_id,))
    assert gaps.fetchone() == (0,)
    rows = subset.admin.execute("select step, error_code from app.jobs where case_id = %s", (subset.case_id,))
    for step, error in rows.fetchall():
        assert step in {*STEPS, "scan", None} and error is None, "a job row holds more than a step code"


class ByWords:
    """Answers by what a call is about: T-07b finds two references on the letter and nothing elsewhere;
    T-07c finds the scan on page 2 and no page for the colonoscopy report. Never prints a prompt."""

    def __init__(self, source_key: str) -> None:
        self.source_key = source_key
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.task_id == "T-07c":
            matched = 2 if "Ultrasound" in request.text and "Colonoscopy" not in request.text else None
            return response({"reference_id": "r", "matched_page": matched, "confidence": 0.9})
        page = request.text.split("\n\n", 1)[-1] if request.text.startswith("Previous page header") else request.text
        if "Colonoscopy" not in page:  # the previous page's header is context, not this page's words
            return empty_answer(self.source_key)
        return response(
            {
                "source_type": self.source_key,
                "printed_dates": [],
                "facts": [],
                "references_out": [
                    {
                        "described_document": "Colonoscopy report (lower bowel)",
                        "date": None,
                        "word_ids": COLONOSCOPY_WORDS,
                    },
                    {"described_document": "Ultrasound scan", "date": None, "word_ids": ULTRASOUND_WORDS},
                ],
                "availability_evidence": [],
                "actions_requested_of_clinician": [],
                "entries": [],
            }
        )


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
async def test_a_reference_with_no_page_is_missing_and_one_with_a_page_is_not(
    combo: Combo,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    pack = default_library().combine(*combo.codes)
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    bedrock = ByWords(pack.source_types()[0]["key"])
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=bedrock,
    ) as h:
        await h.upload(text_pdf([LETTER, SCAN]))
        assert set((await h.drain()).values()) == {"done"}
        assert len(t07c_requests(h)) == 2
        (item,) = items_of(h)
        assert item["bundle_page_no"] == 1 and list(item["word_ids"]) == COLONOSCOPY_WORDS
        assert item["status"] == "open" and item["reference_date"] is None
        same = await description(h, item) == "Colonoscopy report"
        assert same, "page 1: a description with words not on the page was kept"


async def test_missing_items_change_only_as_allowed_and_only_by_the_request_action(
    admin: psycopg.Connection, seed: Seed, api_db: Database, worker_dsn: str
) -> None:
    expert, viewer, stranger = seed.person(), seed.person(), seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert)
    seed.case_member(case_id, viewer, "viewer", added_by=expert)
    doc = seed.document(case_id)
    page_id = admin.execute(  # type: ignore[index]
        "insert into app.pages (case_id, document_id, bundle_page_no) values (%s, %s, 1) returning id", (case_id, doc)
    ).fetchone()[0]
    worker_db = Database(worker_dsn, max_size=1)
    await worker_db.open()
    try:
        async with worker_db.job(case_id) as conn:
            cur = await conn.execute(
                "insert into app.missing_items (case_id, citing_page_id, reference_key, word_ids, description)"
                " values (%s, %s, 'r1', %s, %s) returning id",
                (case_id, page_id, ["p1w1"], b"sealed"),
            )
            row = await cur.fetchone()
            assert row is not None
            item_id = row[0]
            await conn.execute(
                "insert into app.record_gaps (case_id, source_type, fact_kind, gap_start, gap_end, last_page_id)"
                " values (%s, 's', 'k', '2024-02-01', '2024-12-31', %s)",
                (case_id, page_id),
            )
    finally:
        await worker_db.close()
    change = "update app.missing_items set status = 'not_available', reason = 'x', version = 1 where id = %s"
    for person in (viewer, stranger):
        async with api_db.request(scope_for(person)) as conn:
            assert (await conn.execute(change, (item_id,))).rowcount == 0
    async with api_db.request(scope_for(viewer)) as conn:
        seen = await conn.execute("select count(*) from app.missing_items where id = %s", (item_id,))
        assert await seen.fetchone() == (1,)
        gaps = await conn.execute("select count(*) from app.record_gaps where case_id = %s", (case_id,))
        assert await gaps.fetchone() == (1,)
    async with api_db.request(scope_for(stranger)) as conn:
        seen = await conn.execute("select count(*) from app.missing_items where id = %s", (item_id,))
        assert await seen.fetchone() == (0,)
    async with api_db.request(scope_for(expert)) as conn:
        assert (await conn.execute(change, (item_id,))).rowcount == 1
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("update app.missing_items set description = 'x' where id = %s", (item_id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("update app.record_gaps set gap_end = gap_start where case_id = %s", (case_id,))
    with pytest.raises(psycopg.Error) as refused:
        with admin.transaction():
            admin.execute("update app.missing_items set status = 'received' where id = %s", (item_id,))
    assert refused.value.sqlstate == "AV409"
    assert admin.execute("select status from app.missing_items where id = %s", (item_id,)).fetchone() == (
        "not_available",
    )
