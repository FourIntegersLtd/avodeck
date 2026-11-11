"""AI audit, step 14 (I, integrity): what the pipeline stores and shows when the model, or the pages, are hostile.

The model is a scripted fake and a steered model is its hostile ANSWER. Each test states the SAFE behaviour and has a
positive control (the honest path works), so a refusal is never a vacuous pass. Dates, the patient the page belongs to,
where an entry starts and ends, copies, crossed-out words, the label a page is given, the raw answer, the blind read and
what a report may cite. Synthetic text only; assertion messages name pages and counts, never page words (rule 6).
"""

# ruff: noqa: F811  (the shared fixtures are imported from the first file)

from __future__ import annotations

import contextlib
import dataclasses
import datetime as dt
import json
from collections.abc import AsyncIterator, Sequence
from typing import Any

import psycopg
import pytest

from app.core.ai.ocr import OcrModel, OcrPage, OcrResult, OcrWord
from app.core.contracts.facts import citable_facts
from app.features import pipeline
from app.features.pipeline import paths
from app.features.pipeline.schemas import SortedPage
from app.features.pipeline.wiring import current
from app.features.reader import repo as reader_repo
from app.features.reader.log import ReaderPageReadLog
from tests.repository.test_pipeline_fanout_support import scanned_document
from tests.repository.test_pipeline_quarantine import OTHER_PAGE, bundle
from tests.repository.test_pipeline_support import Harness, facts_of, text_pdf
from tests.security.test_pipeline_ai_injection import (  # noqa: F401  (fixtures)
    ENTRY_KIND,
    KIND,
    PACK,
    SOURCES,
    Fake,
    Pages,
    Start,
    analysis,
    blank,
    count,
    fact,
    fact_values,
    pages_answer,
    process,
    start,
    storage,
    stored_plaintext,
    whole,
)
from tests.support.ai_injection import CANARY
from tests.support.db import scope_for

pytestmark = pytest.mark.anyio


# --- a scanned page read by a scripted OCR (the pipeline has no way to see crossed-out words otherwise) ------------


class ScriptedOcr:
    """Reads every page of the file as the words it was given (ids p1w1, p1w2 ...), one line per `line` change."""

    endpoint = "https://ocr.example.test"

    def __init__(self, result: OcrResult) -> None:
        self.result = result

    async def analyse(self, document: bytes, model: OcrModel = "layout", *, locale: str | None = None) -> OcrResult:
        return self.result


def ocr_word(n: int, text: str, *, struck: bool = False, line: int = 1) -> OcrWord:
    x, y = 0.2 + 0.5 * n % 7, 0.5 + 0.4 * line
    return OcrWord(
        id=f"p1w{n}",
        text=text,
        polygon=(x, y, x + 0.4, y, x + 0.4, y + 0.2, x, y + 0.2),
        confidence=0.99,
        struck=struck,
    )


def scripted(words: Sequence[OcrWord]) -> ScriptedOcr:
    return ScriptedOcr(
        OcrResult(model="layout", pages=(OcrPage(number=1, width=8.5, height=11.0, words=tuple(words)),))
    )


@contextlib.asynccontextmanager
async def reading_with(ocr: Any) -> AsyncIterator[None]:
    """The pipeline's services with this OCR, for one case (the last harness opened owns the wiring)."""
    with pipeline.wiring.use(dataclasses.replace(current(), ocr=ocr)):
        yield


def entries_of(h: Harness) -> list[dict[str, Any]]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    return cur.execute(
        "select p.bundle_page_no, e.kind, e.first_n, e.last_n, e.author_role, e.specialty, e.event_date, e.date_basis"
        " from app.record_entries e join app.pages p on p.id = e.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s)"
        " order by p.bundle_page_no, e.first_n",
        (h.case_id,),
    ).fetchall()


# --- dates ----------------------------------------------------------------------------------------------------------


async def test_I01_a_date_in_the_answer_never_becomes_a_date_that_is_not_printed(start: Start) -> None:
    """A fact's date is the printed date its `date` field points at, read by code. A steered model that points at a date
    that is not on the page gets no date for the fact (flagged `please_check`, so it can only make a fact need a look),
    and its `printed_dates` list is not stored. The entry is dated from the words it covers,
    whatever the answer says."""
    pointer = {2: "1999-09-09", 3: "1999-09-09", 4: "08/06/2024"}

    def t07b(pages: Pages) -> dict[str, Any]:
        (no, words), *_ = pages.items()
        fields = {"date": pointer[no]} if no in pointer else None
        facts = [fact(words, len(words) - 1, len(words), fields=fields)]
        return pages_answer(blank(no, facts=facts, entries=[whole(words)], printed_dates=["1999-09-09"]))

    h = await start(Fake(t07b))
    await process(
        h,
        text_pdf(
            [
                ["Seen on 05/04/2024 with cough today"],
                ["Seen with cough today by the nurse"],
                ["Seen on 07/05/2024 with cough today"],
                ["Seen on 08/06/2024 with cough today"],
            ]
        ),
    )
    by_page = {f["bundle_page_no"]: f for f in facts_of(h)}
    assert sorted(by_page) == [1, 2, 3, 4], "positive control: every page's fact was stored"
    assert by_page[1]["event_date"] == dt.date(2024, 4, 5), "positive control: the printed date was used"
    assert by_page[4]["event_date"] == dt.date(2024, 6, 8), (
        "positive control: a pointer at a printed date is read by code"
    )
    for n in (2, 3):
        assert by_page[n]["event_date"] is None, f"page {n}: a date that is not printed is not used"
        assert by_page[n]["date_basis"] == "please_check", f"page {n}: it is flagged for a person"
    assert dt.date(1999, 9, 9) not in {f["event_date"] for f in by_page.values()}
    entries = {e["bundle_page_no"]: e["event_date"] for e in entries_of(h)}
    assert entries == {1: dt.date(2024, 4, 5), 2: None, 3: dt.date(2024, 5, 7), 4: dt.date(2024, 6, 8)}
    assert all(r["printed_dates"] != [dt.date(1999, 9, 9)] for r in analysis(h).values()), (
        "the model's list is not stored"
    )


# --- wrong-patient attribution --------------------------------------------------------------------------------------


async def test_I02_the_model_cannot_say_whose_record_a_page_is(start: Start) -> None:
    """The schema has no field for whose record a page is, and a steered model that adds one is refused. Which pages
    are suggested as another person's comes from identifiers read by code, the same whatever the model says about
    them; the suggestion changes nothing until the named expert confirms it."""
    page_no = OTHER_PAGE

    def steered(pages: Pages) -> dict[str, Any]:
        return pages_answer(*(blank(n, patient="a different person", belongs_to_this_case=n != page_no) for n in pages))

    honest = await start(Fake())
    await process(honest, text_pdf(bundle("gb-eaw")))
    honest_summary = (await honest.client.get(f"/v1/cases/{honest.case_id}/bundle/summary")).json()
    suggested = [(s["page_no"], s["reason"]) for s in honest_summary["quarantine_suggestions"]]
    assert suggested == [(page_no, "identifiers_differ")], "positive control: code found the other person's page"

    hostile = await start(Fake(steered))
    await process(hostile, text_pdf(bundle("gb-eaw")))
    rows = analysis(hostile)
    assert all(r["reason"] == "invalid_output" for r in rows.values()), "the extra field is refused, not read"
    summary = (await hostile.client.get(f"/v1/cases/{hostile.case_id}/bundle/summary")).json()
    assert [(s["page_no"], s["reason"]) for s in summary["quarantine_suggestions"]] == suggested
    assert summary["quarantined"] == [] and summary["pages"] == honest_summary["pages"] == 5
    assert not any(p["quarantined"] for p in hostile.pages()), "no page was set aside by the model"
    assert stored_plaintext(hostile, "a different person") == []


# --- merged events and entry bounds ---------------------------------------------------------------------------------


async def test_I03_entries_stay_inside_the_page_and_never_overlap(start: Start) -> None:
    """A steered answer gives entries that overlap, run backwards, start at a word that is not on the page or have a
    kind outside the pack. The stored entries are all on the page, in order and apart; the one that overlaps is
    trimmed to the words after the first, never merged over them; a clean entry beside them is kept."""

    def t07b(pages: Pages) -> dict[str, Any]:
        (no, words), *_ = pages.items()
        n = len(words)

        def entry(first: int, last: int, **extra: Any) -> dict[str, Any]:
            return {**whole(words[first - 1 : last]), **extra}

        entries = [
            entry(1, 6),
            entry(4, 9),  # overlaps the first
            {**entry(9, 9), "first_word_id": words[8][0], "last_word_id": words[2][0]},  # backwards
            {**entry(1, 3), "first_word_id": "p1w9999", "last_word_id": "p1w10000"},  # not on the page
            {**entry(1, 3), "first_word_id": "p7w1", "last_word_id": "p7w2"},  # another page's ids
            entry(1, 3, kind="invented_kind"),
            entry(n - 2, n),
        ]
        return pages_answer(blank(no, entries=entries, facts=[fact(words, 1, 2)]))

    h = await start(Fake(t07b))
    await process(h, text_pdf([["Seen with cough today by the nurse who advised rest and fluids daily at home"]]))
    stored = entries_of(h)
    words = len(
        h.admin.execute(
            "select 1 from app.ocr_words w join app.pages p on p.id = w.page_id"
            " where p.bundle_id = (select bundle_id from app.cases where id = %s)",
            (h.case_id,),
        ).fetchall()
    )
    assert len(stored) >= 3 and [e["first_n"] for e in stored][:2] == [1, 7], (
        "positive control: entries were stored, the overlap trimmed"
    )
    assert all(1 <= e["first_n"] <= e["last_n"] <= words for e in stored), "every entry is on the page"
    assert all(a["last_n"] < b["first_n"] for a, b in zip(stored, stored[1:], strict=False)), "no two entries overlap"
    assert (stored[0]["first_n"], stored[0]["last_n"]) == (1, 6), "the first keeps its words"
    assert (stored[1]["first_n"], stored[1]["last_n"]) == (7, 9), "the overlapping one starts after it"
    assert stored[-1]["last_n"] == words, "the clean entry at the end was kept"
    assert {e["kind"] for e in stored} <= {str(k["key"]) for k in PACK.entry_kinds()}


async def test_I04_a_whole_page_entry_over_several_dated_visits_is_cut_by_code_never_given_one_pick(
    start: Start,
) -> None:
    """One steered entry over a page of six dated visits would give them all one date. Code cuts it at the dated lines
    (the ends stay with their neighbour: page furniture is guarded), so no entry is dated from words outside it, an
    entry that still holds two dates is left for a person (`please_check`, no date), and the pieces cover exactly the
    words the model's entry did (provenance is unchanged)."""
    days = [
        dt.date(2024, 4, 5),
        dt.date(2024, 5, 12),
        dt.date(2024, 6, 20),
        dt.date(2024, 7, 1),
        dt.date(2024, 8, 9),
        dt.date(2024, 9, 2),
    ]
    lines = [f"{d:%d/%m/%Y} Seen with cough today" for d in days]  # five words to a line
    kind = next(str(k["key"]) for k in PACK.entry_kinds() if k["key"] != PACK.letter_entry_kind())

    def t07b(pages: Pages) -> dict[str, Any]:
        (no, words), *_ = pages.items()
        return pages_answer(blank(no, entries=[{**whole(words), "kind": kind}], facts=[fact(words, 2, 3)]))

    h = await start(Fake(t07b))
    await process(h, text_pdf([lines]))
    stored = entries_of(h)
    assert len(stored) >= 3, "positive control: the entry was cut into visits by code"
    assert (stored[0]["first_n"], stored[-1]["last_n"]) == (1, 30), "the pieces cover exactly the model's entry"
    assert all(a["last_n"] + 1 == b["first_n"] for a, b in zip(stored, stored[1:], strict=False)), "no gap, no overlap"
    for entry in stored:
        inside = {days[(n - 1) // 5] for n in range(entry["first_n"], entry["last_n"] + 1)}
        if len(inside) == 1:
            assert entry["event_date"] in (None, *inside), "dated from its own words"
        else:
            assert entry["event_date"] is None or entry["event_date"] in inside
    assert all(
        e["event_date"] is not None
        for e in stored
        if len({days[(n - 1) // 5] for n in range(e["first_n"], e["last_n"] + 1)}) == 1
    )
    assert any(e["event_date"] is not None for e in stored), "positive control: a visit was dated"


# --- duplicates -----------------------------------------------------------------------------------------------------


async def test_I05_a_copy_is_set_aside_by_code_never_deleted_and_still_counted(start: Start) -> None:
    """Pages that carry the same words are set aside as copies of the first by code. A copy keeps its number, its words
    and its image, is listed, and is counted; a steered model that gives different pages the same answer does not
    make a copy of anything, and an answer that says "same as page 1" is refused."""
    letter = ["Practice letter about the review appointment on 03/04/2024", "Please book a further review in six weeks"]
    other = ["Blood pressure 128/82 and weight 71 kg were recorded", "Please book a further review in six weeks"]
    h = await start(Fake())
    await process(h, text_pdf([letter, other, letter]))
    pages = {p["bundle_page_no"]: p for p in h.pages()}
    assert pages[3]["duplicate_of_page_id"] == pages[1]["id"], "positive control: the copy was found by code"
    assert pages[1]["duplicate_of_page_id"] is None and pages[2]["duplicate_of_page_id"] is None
    assert sorted(pages) == [1, 2, 3], "the copy keeps its number"
    words = h.admin.execute("select count(*) from app.ocr_words where page_id = %s", (pages[3]["id"],)).fetchone()
    assert words is not None and words[0] > 0, "the copy's words are kept"
    listed = (await h.client.get(f"/v1/cases/{h.case_id}/duplicates")).json()["duplicates"]
    assert [(d["page_no"], d["copy_of_page_no"], d["status"]) for d in listed] == [(3, 1, "suggested")]
    thumb = await h.client.get(f"/v1/cases/{h.case_id}/pages/3/thumbnail")
    assert thumb.status_code == 200, "the copy can still be looked at"
    summary = (await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")).json()
    assert summary["duplicates"] == 1 and summary["pages"] >= 2

    def same_answer(pages_: Pages) -> dict[str, Any]:
        return pages_answer(*(blank(n, copy_of_page_no=1, duplicate=True) for n in pages_))

    steered = await start(Fake(same_answer))
    await process(steered, text_pdf([letter, other, letter]))
    assert all(r["reason"] == "invalid_output" for r in analysis(steered).values()), "positive control: refused"
    after = {p["bundle_page_no"]: p for p in steered.pages()}
    assert after[3]["duplicate_of_page_id"] == after[1]["id"], "the copy is still found by code"
    assert after[2]["duplicate_of_page_id"] is None

    distinct = await start(Fake())
    await process(distinct, text_pdf([letter, other]))
    assert all(p["duplicate_of_page_id"] is None for p in distinct.pages()), "different pages are never copies"


# --- crossed-out words ----------------------------------------------------------------------------------------------


async def test_I06_crossed_out_words_are_never_cited_or_dated(start: Start) -> None:
    """A fact that cites a crossed-out word, alone or inside a range, is refused; a date that is crossed out dates
    nothing. The fact beside them, on live words, is kept and the entry takes the date that is still on the page."""
    words = [
        ocr_word(1, "Seen"),
        ocr_word(2, "with"),
        ocr_word(3, "cough"),
        ocr_word(4, "Amoxicillin", struck=True),
        ocr_word(5, "500mg", struck=True),
        ocr_word(6, "01/01/2020", struck=True),
        ocr_word(7, "Doxycycline"),
        ocr_word(8, "100mg"),
        ocr_word(9, "on"),
        ocr_word(10, "05/04/2024"),
    ]

    def t07b(pages: Pages) -> dict[str, Any]:
        (no, seen), *_ = pages.items()
        by_id = dict(seen)
        assert {"p1w4", "p1w5"} <= set(by_id), "positive control: the model was shown the crossed-out words"
        only_struck = fact(seen, 4, 5)
        across = fact(seen, 3, 5)
        live = fact(seen, 7, 8)
        return pages_answer(blank(no, facts=[only_struck, across, live], entries=[whole(seen)]))

    fake = Fake(t07b)
    h = await start(fake, batch_size="1")
    async with reading_with(scripted(words)):
        await process(h, scanned_document(1))
    assert fact_values(h) == ["Doxycycline 100mg"], "only the fact on live words is kept"
    row = analysis(h)[1]
    assert dt.date(2020, 1, 1) not in row["printed_dates"], "a crossed-out date is not a printed date"
    stored = entries_of(h)
    assert [e["event_date"] for e in stored] == [dt.date(2024, 4, 5)], "the entry takes the date still on the page"


# --- the label a page is given --------------------------------------------------------------------------------------


async def test_I07_the_pages_source_label_is_shown_as_a_suggestion() -> None:
    """The label is the model's opinion of where a page is from. Like a fact, it should carry a status the expert can
    see and correct. (Introspection: the sorted-pages answer has a field that says so.)"""
    fields = set(SortedPage.model_fields)
    assert {f for f in fields if any(w in f for w in ("suggest", "confirm", "checked", "status"))}, sorted(fields)


async def test_I08_every_source_label_the_pack_allows_is_counted_by_source_and_none_is_lost(start: Start) -> None:
    """Whatever label a steered model gives, the page is counted once, under that label (or under none): the by-source
    counts add up to the pages. (What the label drives: own-record availability for `own_record_sources` only.)"""
    labels = [*SOURCES, None]
    h = await start(Fake(lambda pages: pages_answer(*(blank(n, source=labels[n - 1]) for n in pages))))
    await process(h, text_pdf([["Seen with cough today by the nurse"]] * len(labels)))
    summary = (await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")).json()
    counted = {s["source_type"]: s["pages"] for s in summary["by_source"]}
    assert all(counted.get(label, 0) >= 1 for label in SOURCES), "positive control: each label was stored"
    assert sum(counted.values()) == summary["pages"] == len(labels)
    own = sorted(PACK.own_record_sources())
    assert own == [SOURCES[0]] and len(own) < len(SOURCES), "only one label makes a page the expert's own record"


# --- the raw answer -------------------------------------------------------------------------------------------------


async def test_I09_the_raw_answer_is_sealed_and_holds_what_the_model_said(start: Start) -> None:
    """What the model said (including the free text the server does not read) is kept sealed under the page, never in
    the clear in any table."""
    marker = "RoleMarker-Alpha"

    def t07b(pages: Pages) -> dict[str, Any]:
        (no, words), *_ = pages.items()
        return pages_answer(blank(no, facts=[fact(words, 3, 4, role=marker)]))

    h = await start(Fake(t07b))
    await process(h, text_pdf([["Seen with cough today by the nurse"]]))
    page_id = h.pages()[0]["id"]
    sealed = h.admin.execute("select extraction from app.page_analysis where page_id = %s", (page_id,)).fetchone()
    assert sealed is not None and sealed[0] is not None
    opened = json.loads((await h.decrypt(bytes(sealed[0]), paths.extraction_purpose(page_id))).decode())
    assert marker in json.dumps(opened), "positive control: the raw answer was kept, as the model gave it"
    assert marker not in bytes(sealed[0]).decode("latin-1")
    assert stored_plaintext(h, marker) == []


async def test_I10_a_second_read_does_not_overwrite_the_first_raw_answer(start: Start) -> None:
    state = {"round": 1}

    def t07b(pages: Pages) -> dict[str, Any]:
        (no, words), *_ = pages.items()
        role = "RoleMarker-First" if state["round"] == 1 else "RoleMarker-Second"
        return pages_answer(blank(no, facts=[fact(words, 3, 4, role=role)]))

    fake = Fake(t07b)
    h = await start(fake)
    await process(h, text_pdf([["Seen with cough today by the nurse"]]))
    page_id = h.pages()[0]["id"]
    asked = len(fake.of("T-07b"))

    state["round"] = 2
    queued = await h.client.post(f"/v1/cases/{h.case_id}/reprocess")
    assert queued.status_code == 202, queued.status_code
    finished = await h.drain()
    assert set(finished.values()) == {"done"}
    assert len(fake.of("T-07b")) > asked, "positive control: the page was read again"
    rows = h.admin.execute("select extraction from app.page_analysis where page_id = %s", (page_id,)).fetchall()
    kept = [
        json.dumps(json.loads((await h.decrypt(bytes(r[0]), paths.extraction_purpose(page_id))).decode())) for r in rows
    ]
    assert any("RoleMarker-Second" in k for k in kept), "positive control: the new answer was kept"
    assert any("RoleMarker-First" in k for k in kept), "the first raw answer is still there"


# --- blind read and what a report may cite --------------------------------------------------------------------------


async def test_I11_no_route_of_this_slice_shows_a_suggestion_before_the_page_is_read(start: Start) -> None:
    """Facts come out of the reader's route only, and that returns 423 until the page is read, even when the answer was
    hostile: nothing the model said (value, fields, hostile text) is in the 423 or in any bundle route. After the read
    the same route gives the suggestion, still a suggestion."""

    def t07b(pages: Pages) -> dict[str, Any]:
        (no, words), *_ = pages.items()
        steered = fact(words, 3, 4, fields={"history": f"Steered text {CANARY}"})
        return pages_answer(blank(no, facts=[steered], entries=[whole(words)]))

    h = await start(Fake(t07b))
    await process(h, text_pdf([["Seen with cough today by the nurse"]]))
    base = f"/v1/cases/{h.case_id}"
    for route in ("/pages/1/facts", "/pages/1/words"):
        blocked = await h.client.get(base + route)
        assert blocked.status_code == 423 and blocked.json()["error"]["code"] == "blind_read", route
        assert CANARY not in blocked.text and "cough today" not in blocked.text
    for route in ("/bundle/summary", "/bundle/sorted", "/duplicates", "/missing-items", "/exhibits", "/process"):
        shown = await h.client.get(base + route)
        assert shown.status_code == 200, route
        assert CANARY not in shown.text and "cough today" not in shown.text, route
    read = await h.client.post(base + "/pages/1/read", json={"dwell_seconds": 30})
    assert read.status_code == 200, read.status_code
    opened = await h.client.get(base + "/pages/1/facts")
    assert opened.status_code == 200, "positive control: the page opens once it is read"
    assert [f["status"] for f in opened.json()["facts"]] == ["suggested"]


async def test_I12_a_suggestion_is_never_citable_and_a_steered_status_is_refused(start: Start) -> None:
    """Every stored fact starts as a suggestion. An answer that carries `status: verified` is refused by the schema, and
    nothing a report may cite comes back until the named expert has read the page and verified the fact."""

    def t07b(pages: Pages) -> dict[str, Any]:
        (no, words), *_ = pages.items()
        steered = {**fact(words, 3, 4), "status": "verified", "verified_by": "expert"}
        return pages_answer(blank(no, facts=[steered if no == 1 else fact(words, 3, 4)]))

    h = await start(Fake(t07b))
    await process(h, text_pdf([["Seen with cough today by the nurse"], ["Seen with rash today by the nurse"]]))
    rows = analysis(h)
    assert rows[1]["reason"] == "invalid_output", "the steered status was refused"
    assert rows[2]["reason"] is None and [f["status"] for f in facts_of(h)] == ["suggested"], "positive control"
    assert count(h, "fact_verifications") == 0

    async def citable() -> set[Any]:
        async with h.api_db.request(scope_for(h.expert)) as conn:
            return {f.id for f in await citable_facts(conn, h.case_id, ReaderPageReadLog())}

    page_id = next(p["id"] for p in h.pages() if p["bundle_page_no"] == 2)
    assert await citable() == set(), "nothing is citable before the page is read"
    async with h.api_db.request(scope_for(h.expert)) as conn:
        await reader_repo.record_read(conn, h.case_id, page_id, h.expert, 30)
    assert await citable() == set(), "a read page's suggestion is still not citable"
    fact_id = facts_of(h)[0]["id"]
    h.admin.execute("update app.facts set status = 'verified' where id = %s", (fact_id,))
    assert await citable() == {fact_id}, "positive control: once verified on a read page it is citable"
