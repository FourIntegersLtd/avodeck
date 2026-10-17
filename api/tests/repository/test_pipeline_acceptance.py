"""Slice A acceptance: the whole 900-page synthetic bundle, processed once for this module (slow).

Run with `RUN_SLOW=1` (or `-m slow` with it set). The bundle and its recorded OCR and model answers
come from the generator (`make eval-data`); the expected files say what a correct run finds.
Messages name pages and counts only, never page text (rule 6).
"""

from __future__ import annotations

import datetime as dt
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import (
    BUNDLE,
    Harness,
    date_score,
    expected,
    facts_of,
    from_struck_text,
    harness,
    identifier_leaked,
    page_words,
    precision,
    recorded_book,
    t07b_requests,
)
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = [
    pytest.mark.anyio,
    pytest.mark.slow,
    pytest.mark.skipif(not os.environ.get("RUN_SLOW"), reason="the 900-page bundle runs with RUN_SLOW=1"),
]


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    """Module scope, so the bundle is processed once; every async fixture here asks for it, so the
    plugin runs them even for the plain tests below."""
    return "asyncio"


@pytest.fixture(scope="module")
async def bundle_storage(anyio_backend: str, auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture(scope="module")
async def bundle_api_db(anyio_backend: str, api_dsn: str) -> AsyncIterator[Database]:
    database = Database(api_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture(scope="module")
async def bundle(
    anyio_backend: str,
    admin: psycopg.Connection,
    bundle_api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    bundle_storage: SupabaseStorage,
) -> AsyncIterator[Harness]:
    """records.pdf uploaded and processed once under the launch packs."""
    seed = Seed(admin)
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    store = FixtureStore(BUNDLE / "recordings")
    case = expected("case.json")
    async with harness(
        admin=admin,
        api_db=bundle_api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=bundle_storage,
        expert=expert,
        case_id=case_id,
        ocr_store=store,
        bedrock=recorded_book(store),
        names=case["case_names"],
        dates_of_birth=[dt.date.fromisoformat(case["dob"])],
    ) as h:
        await h.upload((BUNDLE / "records.pdf").read_bytes())
        finished = await h.drain(timeout=3600)
        assert set(finished.values()) == {"done"}, finished
        yield h


def numbered(h: Harness) -> dict[int, dict[str, Any]]:
    return {int(p["bundle_page_no"]): p for p in h.pages()}


@pytest.fixture(scope="module")
def pages(bundle: Harness) -> Iterator[dict[int, dict[str, Any]]]:
    yield numbered(bundle)


def test_every_page_is_numbered_once_in_order(pages: dict[int, dict[str, Any]]) -> None:
    assert sorted(pages) == list(range(1, 901))
    assert len({uuid.UUID(str(p["id"])) for p in pages.values()}) == 900


def test_born_digital_pages_use_their_text_layer_and_make_no_ocr_call(
    bundle: Harness, pages: dict[int, dict[str, Any]]
) -> None:
    wanted = {int(r["page"]): r["text_source"] for r in expected("text_source.json")}
    wrong = sorted(n for n, source in wanted.items() if pages[n]["text_source"] != source)
    assert wrong == [], f"pages with the wrong text source: {wrong}"
    embedded = sum(1 for s in wanted.values() if s == "embedded")
    assert bundle.ocr is not None
    assert len(bundle.ocr.sent) == len(wanted) - embedded, "OCR calls do not match the scanned pages"


def test_low_confidence_pages_are_queued_as_unreadable(pages: dict[int, dict[str, Any]]) -> None:
    wanted = set(expected("unreadable.json"))
    found = {n for n, p in pages.items() if p["unreadable"]}
    assert found == wanted, f"missing {sorted(wanted - found)}, extra {sorted(found - wanted)}"


def test_no_identifier_reaches_the_model(bundle: Harness) -> None:
    sent = t07b_requests(bundle)
    copies = len(expected("duplicates.json"))  # exact copies share their words, so their subject
    assert len({r.subject for r in sent}) >= 900 - copies, "a page was not sent for extraction"
    recognise = default_library().combine(*LAUNCH.codes).identifier_recognisers()
    assert not identifier_leaked([r.text for r in sent], expected("case.json"), recognise)


def test_source_types_are_right_on_at_least_98_percent_of_pages(pages: dict[int, dict[str, Any]]) -> None:
    wanted = {int(r["page"]): r["source_type"] for r in expected("sources.json")}
    right = sum(1 for n, source in wanted.items() if pages[n]["source_type"] == source)
    assert right / len(wanted) >= 0.98, f"{right} of {len(wanted)} pages have the right source"


def test_fact_precision_is_at_least_095(bundle: Harness, pages: dict[int, dict[str, Any]]) -> None:
    copies = {int(d["page"]) for d in expected("duplicates.json")}
    found = [(int(f["bundle_page_no"]), f["kind"], f["value"]) for f in facts_of(bundle)]
    found = [f for f in found if f[0] not in copies]
    wanted = expected("facts.json")
    score, count = precision(found, wanted)
    assert count >= 0.9 * len(wanted), f"only {count} facts found of {len(wanted)} expected"
    assert score >= 0.95, f"precision {score:.3f} over {count} facts"


async def test_no_fact_comes_from_crossed_out_text(bundle: Harness, pages: dict[int, dict[str, Any]]) -> None:
    struck_text: dict[int, list[str]] = {}
    for row in expected("struck_out.json"):
        struck_text.setdefault(int(row["page"]), []).extend(row["struck_out"])
    bad: set[int] = set()
    for fact in facts_of(bundle):
        n = int(fact["bundle_page_no"])
        if from_struck_text(fact["value"] or "", struck_text.get(n, [])):
            bad.add(n)
    for n in sorted({int(f["bundle_page_no"]) for f in facts_of(bundle)} & set(struck_text)):
        words = await page_words(bundle, pages[n]["id"])
        struck = {w for w, (_, s) in words.items() if s}
        if any(set(f["word_ids"]) & struck for f in facts_of(bundle) if int(f["bundle_page_no"]) == n):
            bad.add(n)
    assert not bad, f"facts from crossed-out text on pages {sorted(bad)}"


def test_planted_wrong_answers_are_retried_and_then_need_a_person(
    bundle: Harness, pages: dict[int, dict[str, Any]]
) -> None:
    rows: dict[int, bool] = dict(
        bundle.admin.execute(
            "select p.bundle_page_no, a.needs_human from app.page_analysis a join app.pages p on p.id = a.page_id"
            " where a.case_id = %s",
            (bundle.case_id,),
        ).fetchall()
    )
    assert rows[701] is True and rows[113] is False
    needing = sorted(n for n, flag in rows.items() if flag)
    assert len(needing) <= 9, f"pages needing a person: {needing}"


def test_dates_are_read_in_the_pack_date_order(bundle: Harness) -> None:
    rows = [(int(f["bundle_page_no"]), f["value"], f["event_date"], f["date_basis"]) for f in facts_of(bundle)]
    score = date_score(rows)
    assert score.accuracy >= 0.9, f"printed dates right on {score.accuracy:.3f} of {score.printed} facts"
    assert score.coverage >= 0.75, f"dates right on {score.coverage:.3f} of {score.wanted} dated facts"
    planted = [(when, basis) for n, _, when, basis in rows if n == 82]  # printed 03/04/2024: 3 April
    assert (dt.date(2024, 4, 3), "printed") in planted, "page 82: 03/04/2024 was not read as 3 April"
    assert all((when is not None) == (basis == "printed") for _, _, when, basis in rows)


def planted_groups() -> dict[int, int]:
    """Each page's planted group: the page a copy was made from, or the page itself."""
    group = {n: n for n in range(1, 901)}
    for row in expected("duplicates.json"):
        group[int(row["page"])] = int(row["copy_of_page"])
    return group


def test_the_planted_copies_are_set_aside_and_no_other_page_is(pages: dict[int, dict[str, Any]]) -> None:
    group = planted_groups()
    number = {p["id"]: n for n, p in pages.items()}
    set_aside = {n: number[p["duplicate_of_page_id"]] for n, p in pages.items() if p["duplicate_of_page_id"]}
    false = sorted(n for n, original in set_aside.items() if group[n] != group[original])
    assert false == [], f"pages set aside that are not copies: {false}"
    assert len(set_aside) == len(expected("duplicates.json")), f"{len(set_aside)} pages set aside"
    members: dict[int, set[int]] = {}
    for n, g in group.items():
        members.setdefault(g, set()).add(n)
    for g, pages_in in members.items():
        kept = pages_in - set(set_aside)
        assert len(kept) == 1, f"group of page {g}: {len(kept)} pages kept"
    for n, original in set_aside.items():
        assert original < n and original not in set_aside, f"page {n} points at a page that is not the first copy"


def test_a_lab_copy_of_a_result_is_not_set_aside(pages: dict[int, dict[str, Any]]) -> None:
    assert pages[119]["duplicate_of_page_id"] is None and pages[402]["duplicate_of_page_id"] is None


def test_copies_keep_their_pictures_and_are_read_like_any_page(
    bundle: Harness, pages: dict[int, dict[str, Any]]
) -> None:
    copies = {p["id"] for p in pages.values() if p["duplicate_of_page_id"]}
    assert copies and all(pages[n]["image_path"] for n, p in pages.items() if p["id"] in copies)
    unread = bundle.admin.execute(
        "select count(*) from app.page_analysis where case_id = %s and page_id = any(%s) and extracted_at is null",
        (bundle.case_id, list(copies)),
    ).fetchone()
    assert unread == (0,), "a copy was not read"


def test_the_planted_record_gap_is_found_and_no_other(bundle: Harness, pages: dict[int, dict[str, Any]]) -> None:
    """The task list stops in January 2024 while the record runs to December (planted item 2)."""
    from collections import Counter

    from tests.repository.test_pipeline_support import recorded_source_keys

    (planted,) = expected("gaps.json")
    cur = bundle.admin.cursor(row_factory=psycopg.rows.dict_row)
    found = cur.execute("select * from app.record_gaps where case_id = %s", (bundle.case_id,)).fetchall()
    assert len(found) == 1, f"{len(found)} record gaps"
    (gap,) = found
    number = {p["id"]: n for n, p in pages.items()}
    last = number[gap["last_page_id"]]
    kinds = Counter(str(f["kind"]) for f in expected("facts.json") if int(f["page"]) == last)
    assert gap["source_type"] == recorded_source_keys()[planted["source"]]
    assert gap["fact_kind"] == kinds.most_common(1)[0][0], f"page {last}"
    assert gap["gap_start"].strftime("%Y-%m") == planted["gap_from"]
    assert gap["gap_end"].strftime("%Y-%m") == planted["gap_to"]
    assert gap["confirmed"] is None


async def test_the_planted_missing_document_is_listed_and_found_references_are_not(
    bundle: Harness, pages: dict[int, dict[str, Any]]
) -> None:
    """The colonoscopy report mentioned on page 390 is in no page (planted item 3); the other
    references in the bundle are matched to their pages."""
    from app.features.pipeline import paths

    number = {p["id"]: n for n, p in pages.items()}
    rows = bundle.admin.execute(
        "select id, citing_page_id, description, status from app.missing_items where case_id = %s",
        (bundle.case_id,),
    ).fetchall()
    found = []
    for item, citing, sealed, status in rows:
        text = (await bundle.decrypt(bytes(sealed), paths.missing_description_purpose(item))).decode()
        assert status == "open"
        found.append((number[citing], text))
    wanted = [(int(m["page"]), str(m["described_document"])) for m in expected("missing.json")]
    same = sorted(found) == sorted(wanted)
    assert same, f"missing items cite pages {sorted(n for n, _ in found)}"
    asked = [r for r in bundle.bedrock.requests if r.task_id == "T-07c"]
    assert len(asked) == len(expected("references.json")), f"{len(asked)} references asked"
