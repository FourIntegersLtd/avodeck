"""Provenance audit 16, slice A (PV-04, PV-05, PV-07 to PV-11): what the pipeline keeps, logs and flags.

Each test states the SAFE outcome and carries its finding id. Synthetic pages only; assertion messages name pages,
counts and codes, never page words (rule 6).

- PV-04 undoing a quarantine, or setting a page aside, changes fact status: it leaves a who/when/why row in the log.
- PV-05 a suggestion the system withdraws (a re-read supersedes it) is not mistaken for an expert's "not right";
  a page's refusal keeps its own reason code.
- PV-07 the pipeline re-checks the stored bytes (whole-file SHA-256, part count, page file) before it uses them.
- PV-08 a page's place in its file is kept at ingest and never derived again; a bundle's numbers are never reused.
- PV-09 only an expert-confirmed copy is set aside; a suggested copy is still read.
- PV-10 an OCR failure is told apart from a page read as empty; a short page with words and no facts has a light code.
- PV-11 a PDF that states more pages than were ingested is flagged where the expert looks for missing records.
"""

# ruff: noqa: F811  (the shared fixtures are imported from the first file)

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from app.core.ai.ocr import OcrFailed, OcrModel, OcrPage, OcrResult
from app.core.db import Database
from app.core.http import etag
from app.core.packs import default_library
from app.features.pipeline import dates, paths
from tests.repository.test_pipeline_batches_progress import case, pages, parent_row, report  # noqa: F401  (fixture)
from tests.repository.test_pipeline_batches_support import add_batch, add_run, make_job
from tests.repository.test_pipeline_date_repair import storage  # noqa: F401  (fixture)
from tests.repository.test_pipeline_fanout_support import scanned_document
from tests.repository.test_pipeline_support import Harness, facts_of, harness, page_words, t07b_requests, text_pdf
from tests.security.test_pipeline_ai_injection import (  # noqa: F401  (fixtures)
    KIND,
    Fake,
    Pages,
    Start,
    analysis,
    blank,
    fact,
    lines_of,
    pages_answer,
    process,
    start,
    storage,
)
from tests.security.test_pipeline_ai_integrity import ScriptedOcr, reading_with
from tests.support.db import scope_for
from tests.support.facts_seed import insert_fact
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LETTER = [
    "Practice letter about the review appointment on 03/04/2024",
    "Blood pressure 128/82 and weight 71 kg were recorded",
    "Please book a further review in six weeks with the team",
]
OTHER_VALUE = [LETTER[0], "Blood pressure 138/82 and weight 71 kg were recorded", LETTER[2]]


def with_facts(pages: Pages) -> dict[str, Any]:
    """Every page answers one fact over its first two words."""
    return pages_answer(*(blank(n, facts=[fact(words, 1, 2)]) for n, words in pages.items()))


def page_id(h: Harness, n: int) -> uuid.UUID:
    return next(p["id"] for p in h.pages() if p["bundle_page_no"] == n)  # type: ignore[no-any-return]


def quarantine_url(h: Harness, n: int) -> str:
    return f"/v1/cases/{h.case_id}/pages/{page_id(h, n)}/quarantine"


def log_rows(h: Harness, fact_id: uuid.UUID) -> list[dict[str, Any]]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    return cur.execute(
        "select action, person_id, reason, at, seq from app.fact_verifications where fact_id = %s order by seq",
        (fact_id,),
    ).fetchall()


def facts_on(h: Harness, n: int) -> list[dict[str, Any]]:
    return [f for f in facts_of(h) if f["bundle_page_no"] == n]


async def seal_over(h: Harness, path: str, data: bytes) -> None:
    """Replaces a stored object with `data`, sealed correctly for that path: the tampering a stored-file swap makes
    (a wrong-key or wrong-path blob would already be refused by the seal itself)."""
    async with h.api_db.request(scope_for(h.expert)) as conn:
        blob = await h.crypto.encrypt(conn, h.case_id, data, purpose=path)
    await h.storage.put(path, blob)


def document_row(h: Harness, document_id: uuid.UUID) -> dict[str, Any]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    row = cur.execute("select * from app.documents where id = %s", (document_id,)).fetchone()
    assert row is not None
    return row


# --- PV-04: the fact log covers quarantine ---------------------------------------------------------------------------


async def test_PV04_confirming_a_quarantine_logs_who_when_and_why_for_each_fact_it_rejects(start: Start) -> None:
    h = await start(Fake(with_facts))
    await process(h, text_pdf([["Alpha entry recorded today for review"], ["Bravo entry recorded today for review"]]))
    (suggested,) = facts_on(h, 1)
    (checked,) = facts_on(h, 2)
    h.admin.execute("update app.facts set status = 'verified' where id = %s", (checked["id"],))
    assert log_rows(h, suggested["id"]) == [] and log_rows(h, checked["id"]) == [], "control: no log before the change"

    for n in (1, 2):
        done = await h.client.post(quarantine_url(h, n), json={"reason": "not_this_person"})
        assert done.status_code == 200, done.status_code
    for fact_row in (suggested, checked):
        assert {f["status"] for f in facts_on(h, 1 if fact_row is suggested else 2)} == {"rejected"}
        rows = log_rows(h, fact_row["id"])
        page = 1 if fact_row is suggested else 2
        assert len(rows) == 1, f"page {page}: {len(rows)} log rows for the rejected fact"
        assert rows[0]["person_id"] == h.expert, f"page {page}: the log does not say who"
        assert rows[0]["at"] is not None and rows[0]["reason"], f"page {page}: the log does not say when or why"


async def test_PV04_undoing_a_quarantine_logs_the_facts_it_makes_verified_again(start: Start) -> None:
    h = await start(Fake(with_facts))
    await process(h, text_pdf([["Alpha entry recorded today for review"]]))
    (fact_row,) = facts_on(h, 1)
    h.admin.execute("update app.facts set status = 'verified' where id = %s", (fact_row["id"],))
    assert (await h.client.post(quarantine_url(h, 1), json={"reason": "not_this_person"})).status_code == 200
    before = len(log_rows(h, fact_row["id"]))
    undone = await h.client.delete(quarantine_url(h, 1))
    assert undone.status_code == 200, undone.status_code
    assert [f["status"] for f in facts_on(h, 1)] == ["verified"], "control: the fact is verified again"
    rows = log_rows(h, fact_row["id"])
    assert len(rows) == before + 1, "a fact went back to verified with nothing in the log"
    assert rows[-1]["person_id"] == h.expert and rows[-1]["at"] is not None and rows[-1]["reason"]


# --- PV-05: system-withdrawn is not "not right"; refusal codes are kept -----------------------------------------------


async def test_PV05_a_suggestion_withdrawn_by_a_re_read_is_marked_as_the_systems_and_is_not_a_not_right_log_row(
    start: Start,
) -> None:
    h = await start(Fake(with_facts))
    await process(h, text_pdf([["Alpha entry recorded today for review"]]))
    (old,) = facts_on(h, 1)
    assert old["status"] == "suggested"
    h.admin.execute("update app.pages set text_source = null where id = %s", (page_id(h, 1),))
    await h.enqueue("process_pages")
    assert set((await h.drain()).values()) == {"done"}
    now = {f["id"]: f for f in facts_on(h, 1)}
    assert now[old["id"]]["status"] == "rejected", "control: the re-read superseded the suggestion"
    assert len(now) == 2 and [f["status"] for f in now.values() if f["id"] != old["id"]] == ["suggested"]
    (new,) = (f for f in now.values() if f["id"] != old["id"])
    assert now[old["id"]]["withdrawn_reason"], "a withdrawn suggestion cannot be told from an expert's rejection"
    assert new["withdrawn_reason"] is None, "control: a live suggestion is not marked withdrawn"
    assert not [r for r in log_rows(h, old["id"]) if r["action"] == "not_right"], (
        "the system's act is logged as the expert's"
    )


async def test_PV05_each_refused_page_keeps_its_own_refusal_code(start: Start) -> None:
    def t07b(pages: Pages) -> dict[str, Any]:
        (no, words), *_ = pages.items()
        ids = [w for w, _ in words]
        bad: dict[int, dict[str, Any]] = {
            1: {"kind": KIND, "clinician_role": None, "fields": {}, "value": "alpha", "word_ids": ["p9w9"]},
            2: {"kind": KIND, "clinician_role": None, "fields": {}, "value": "zzzz qqqq", "word_ids": ids[:2]},
            3: {
                "kind": KIND,
                "clinician_role": None,
                "fields": {},
                "value": "alpha today",
                "word_ids": [ids[0], ids[2]],
            },
        }
        return pages_answer(blank(no, facts=[bad[no]]))

    h = await start(Fake(t07b))
    await process(
        h,
        text_pdf(
            [
                ["Alpha entry recorded today for review"],
                ["Bravo entry recorded today for review"],
                ["Alpha entry recorded today for review again"],
            ]
        ),
    )
    rows = h.admin.execute(
        "select p.bundle_page_no, a.refusal_codes from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) order by 1",
        (h.case_id,),
    ).fetchall()
    found = {no: codes for no, codes in rows}
    assert found == {1: ["unknown_word"], 2: ["not_in_words"], 3: ["not_contiguous"]}, found
    assert {row["reason"] for row in analysis(h).values()} == {"not_in_words"}, "the published label is unchanged"
    assert all(row["needs_human"] for row in analysis(h).values()), "a refused page still needs a person"


# --- PV-07: bytes are re-checked before they are used ----------------------------------------------------------------


def _other_pdf() -> bytes:
    return text_pdf([["Altered file page one"], ["Altered file page two"]])


async def test_PV07_a_stored_file_whose_bytes_changed_is_failed_clearly_and_never_processed(start: Start) -> None:
    model = Fake(with_facts)
    h = await start(model)
    await process(h, text_pdf([["Alpha entry recorded today for review"]]))
    asked = len(t07b_requests(h))
    assert len(facts_of(h)) == 1, "control: an untouched file is processed"

    document = await h.upload(text_pdf([["Bravo entry recorded today for review"]]), filename="second.pdf", append=True)
    prefix = str(document_row(h, document)["storage_path"])
    (part,) = await h.storage.list(prefix)
    await seal_over(h, part, _other_pdf())
    finished = await h.drain()
    assert set(finished.values()) == {"done"}, sorted(set(finished.values()))
    row = document_row(h, document)
    assert row["unreadable_reason"] == "altered", f"unreadable_reason={row['unreadable_reason']}"
    assert not [p for p in h.pages() if p["document_id"] == document], "pages were made from altered bytes"
    assert len(t07b_requests(h)) == asked, "altered bytes were sent to a model"


async def test_PV07_a_file_stored_in_a_different_number_of_parts_than_was_uploaded_is_failed(start: Start) -> None:
    h = await start(Fake(with_facts))
    await process(h, text_pdf([["Alpha entry recorded today for review"]]))
    document = await h.upload(text_pdf([["Bravo entry recorded today for review"]]), filename="second.pdf", append=True)
    prefix = str(document_row(h, document)["storage_path"])
    (part,) = await h.storage.list(prefix)
    async with h.api_db.request(scope_for(h.expert)) as conn:
        whole = await h.crypto.decrypt(conn, h.case_id, await h.storage.get(part), purpose=part)
    half = len(whole) // 2
    second = f"{part}~"
    await seal_over(h, part, whole[:half])
    await seal_over(h, second, whole[half:])  # the same bytes in the same order: only the part count differs
    await h.drain()
    row = document_row(h, document)
    assert row["unreadable_reason"] == "altered", f"unreadable_reason={row['unreadable_reason']}"
    assert not [p for p in h.pages() if p["document_id"] == document]


async def test_PV07_a_page_file_that_changed_since_it_was_cut_is_not_read(start: Start) -> None:
    h = await start(Fake(with_facts))
    await process(h, text_pdf([["Alpha entry recorded today for review"], ["Bravo entry recorded today for review"]]))
    second = page_id(h, 2)
    before = await page_words(h, second)
    assert before, "control: the page was read"
    await seal_over(h, paths.page_pdf_path(h.case_id, second), text_pdf([["Tampered words on this page now"]]))
    h.admin.execute("update app.pages set text_source = null where id = %s", (second,))
    await h.enqueue("process_pages")
    assert set((await h.drain()).values()) == {"done"}
    now = await page_words(h, second)
    assert not any(text == "Tampered" for text, _ in now.values()), "words were read from a page file that had changed"
    row = h.admin.execute(
        "select a.read_failure, p.unreadable from app.page_analysis a join app.pages p on p.id = a.page_id"
        " where a.page_id = %s",
        (second,),
    ).fetchone()
    assert row == ("altered", True), f"read_failure, unreadable = {row}"


# --- PV-08: the page's place in its file is stored; numbers are never reused ------------------------------------------


async def test_PV08_a_page_keeps_its_index_in_its_file_and_it_cannot_change(
    start: Start, admin: psycopg.Connection
) -> None:
    h = await start(Fake(with_facts))
    await process(h, text_pdf([["Alpha entry recorded today for review"], ["Bravo entry recorded today"], ["Charlie"]]))
    rows = admin.execute(
        "select bundle_page_no, source_page_index from app.pages where id = any(%s) order by bundle_page_no",
        ([p["id"] for p in h.pages()],),
    ).fetchall()
    assert rows == [(1, 0), (2, 1), (3, 2)], rows
    with pytest.raises(psycopg.Error):
        admin.execute("update app.pages set source_page_index = 7 where id = %s", (page_id(h, 2),))


async def test_PV08_a_page_is_cut_from_its_stored_index_not_from_where_its_neighbours_are(
    start: Start, admin: psycopg.Connection
) -> None:
    h = await start(Fake(with_facts))
    await process(
        h,
        text_pdf(
            [
                ["Alpha entry recorded today for review"],
                ["Bravo entry recorded today for review"],
                ["Charlie entry recorded today for review"],
            ]
        ),
    )
    third = page_id(h, 3)
    admin.execute("delete from app.pages where id = %s", (page_id(h, 1),))
    admin.execute(
        "update app.pages set image_path = null, text_source = null where id = any(%s)", ([page_id(h, 2), third],)
    )
    await h.enqueue("process_bundle")
    assert set((await h.drain()).values()) == {"done"}
    words = " ".join(text for text, _ in (await page_words(h, third)).values()).lower()
    assert "charlie" in words and "bravo" not in words, "page 3 was cut from another page's place in the file"


async def test_PV08_a_number_given_up_by_a_deleted_file_is_never_given_to_a_later_page(
    start: Start, admin: psycopg.Connection
) -> None:
    h = await start(Fake(with_facts))
    await process(h, text_pdf([["Alpha entry recorded today for review"], ["Bravo entry recorded today for review"]]))
    second = await h.upload(text_pdf([["Charlie entry recorded today for review"]]), filename="b.pdf", append=True)
    await h.drain()
    assert sorted(p["bundle_page_no"] for p in h.pages()) == [1, 2, 3], "control: numbers run on"
    # Straight in the database: the delete route answers 404 to the expert today (AUDIT-11 NEW-4, no DELETE policy).
    admin.execute("delete from app.uploads where document_id = %s", (second,))
    admin.execute("delete from app.documents where id = %s", (second,))
    await h.upload(text_pdf([["Delta entry recorded today for review"]]), filename="c.pdf", append=True)
    await h.drain()
    assert sorted(p["bundle_page_no"] for p in h.pages()) == [1, 2, 4], "page number 3 was handed out a second time"


# --- PV-09: only a confirmed copy is set aside -----------------------------------------------------------------------


async def test_PV09_a_suggested_copy_is_still_read_and_only_a_confirmed_one_is_set_aside(start: Start) -> None:
    model = Fake()
    h = await start(model)
    await process(h, text_pdf([LETTER, OTHER_VALUE, LETTER]))
    copy = page_id(h, 3)
    status = h.admin.execute("select duplicate_status from app.page_analysis where page_id = %s", (copy,)).fetchone()
    assert status == ("suggested",), f"control: page 3 is a suggested copy, not {status}"
    assert len(t07b_requests(h)) == 3

    async def read_again() -> int:
        h.admin.execute("update app.page_analysis set extracted_at = null where page_id = %s", (copy,))
        asked = len(t07b_requests(h))
        await h.enqueue("process_pages")
        assert set((await h.drain()).values()) == {"done"}
        return len(t07b_requests(h)) - asked

    assert await read_again() == 1, "a suggested copy was left unread because the pipeline suggested it was a copy"
    async with h.api_db.request(scope_for(h.expert)) as conn:
        from app.features.pipeline import repo

        listed = [p.page_no for p in await repo.gap_pages(conn, h.case_id)]
    assert listed == [1, 2, 3], f"gap pages {listed}: a suggested copy was left out of the record"
    confirmed = await h.client.patch(
        f"/v1/cases/{h.case_id}/pages/{copy}/duplicate", json={"action": "confirm"}, headers={"if-match": etag(0)}
    )
    assert confirmed.status_code == 200, confirmed.status_code
    assert await read_again() == 0, "control: a confirmed copy is set aside and not read again"
    async with h.api_db.request(scope_for(h.expert)) as conn:
        from app.features.pipeline import repo

        listed = [p.page_no for p in await repo.gap_pages(conn, h.case_id)]
    assert listed == [1, 2], f"gap pages {listed}"


# --- PV-10: failure and emptiness are different; short pages get a light code -----------------------------------------


class FailingOcr(ScriptedOcr):
    async def analyse(self, document: bytes, model: OcrModel = "layout", *, locale: str | None = None) -> OcrResult:
        raise OcrFailed("503")


async def test_PV10_an_ocr_failure_is_told_apart_from_a_page_read_as_empty(start: Start) -> None:
    h = await start(Fake())
    empty = ScriptedOcr(OcrResult(model="layout", pages=(OcrPage(number=1, width=8.5, height=11.0, words=()),)))
    async with reading_with(FailingOcr(empty.result)):
        await process(h, scanned_document(1), filename="failed.pdf")
    async with reading_with(empty):
        await h.upload(scanned_document(1), filename="empty.pdf", append=True)
        assert set((await h.drain()).values()) == {"done"}
    reasons = {n: (row["reason"], row["needs_human"]) for n, row in analysis(h).items()}
    assert reasons[2] == ("no_words", True), f"control: a page read as empty is {reasons[2]}"
    assert reasons[1] == ("ocr_failed", True), f"an OCR failure is {reasons[1]}"


async def test_PV10_a_short_page_with_words_and_no_facts_has_a_light_code_not_a_flag(start: Start) -> None:
    h = await start(Fake())
    long_page = lines_of(" ".join(f"term{'x' * (i % 5)}{'abcdefghij'[i % 10]}" for i in range(45)))
    await process(h, text_pdf([["Short note only"], long_page]))
    rows = analysis(h)
    assert (rows[2]["reason"], rows[2]["needs_human"]) == ("no_facts", True), "control: a full page with no facts"
    assert (rows[1]["reason"], rows[1]["needs_human"]) == ("few_words", False), (
        f"a short page with words and no facts is {(rows[1]['reason'], rows[1]['needs_human'])}"
    )


# --- PV-11: pages the PDF says it has but that were not ingested -----------------------------------------------------


def declared_more(pages: int, declared: int) -> bytes:
    """A PDF whose page tree states `declared` pages but holds `pages` (same-length edit, so the file still opens)."""
    import io

    import pikepdf

    with pikepdf.open(io.BytesIO(text_pdf([[f"Page {n} of the synthetic file"] for n in range(1, pages + 1)]))) as pdf:
        out = io.BytesIO()
        pdf.save(out, object_stream_mode=pikepdf.ObjectStreamMode.disable)
    raw = out.getvalue()
    edited = raw.replace(f"/Count {pages}".encode(), f"/Count {declared}".encode())
    assert len(edited) == len(raw) and edited != raw
    return edited


async def test_PV11_a_pdf_that_states_more_pages_than_were_ingested_is_flagged_in_the_missing_items(
    start: Start,
) -> None:
    h = await start(Fake())
    await process(h, text_pdf([["Alpha entry recorded today for review"], ["Bravo entry recorded today"]]))
    got = await h.client.get(f"/v1/cases/{h.case_id}/missing-items")
    assert got.status_code == 200 and got.json().get("page_shortfalls") == [], "control: a whole file is not flagged"
    short = await h.upload(declared_more(5, 7), filename="short.pdf", append=True)
    assert set((await h.drain()).values()) == {"done"}
    assert len([p for p in h.pages() if p["document_id"] == short]) == 5, "control: the pages present were ingested"
    got = await h.client.get(f"/v1/cases/{h.case_id}/missing-items")
    flagged = got.json().get("page_shortfalls")
    assert flagged is not None and [(s["document_id"], s["declared"], s["found"]) for s in flagged] == [
        (str(short), 7, 5)
    ], f"page_shortfalls={flagged}"


# --- PV-18: each page read by OCR leaves an `ai_calls` row ------------------------------------------------------------


async def test_PV18_each_page_read_by_ocr_leaves_an_ai_calls_row_with_model_version_and_job(start: Start) -> None:
    h = await start(Fake())
    answer = OcrResult(
        model="layout",
        pages=(OcrPage(number=1, width=8.5, height=11.0, words=()),),
        api_version="2024-11-30",
    )
    async with reading_with(ScriptedOcr(answer)):
        await process(h, scanned_document(2))
    rows = h.admin.execute(
        "select task_id, model_id, api_version, job_id, input_tokens from app.ai_calls"
        " where case_id = %s and task_id like 'ocr.%%'",
        (h.case_id,),
    ).fetchall()
    assert len(rows) == 2, f"{len(rows)} OCR calls were recorded for 2 pages read"
    assert {(r[1], r[2]) for r in rows} == {("layout", "2024-11-30")}, "an OCR call does not say model and version"
    assert all(r[3] is not None for r in rows), "an OCR call is not tied to its job"


# --- PV-02: a date repair on a checked fact leaves a row in the check log ---------------------------------------------


async def test_PV02_dating_a_checked_fact_from_the_pipeline_is_logged_with_before_and_after(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: Any,
) -> None:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    page_id = seed.page(case_id, seed.document(case_id), 1)
    pack = default_library().combine(*LAUNCH.codes)
    kind = pack.fact_kinds()[0]["key"]
    field = dates.date_field(pack, kind)
    assert field is not None
    fact_id = insert_fact(
        admin, case_id, page_id, kind, fields={field: "03/04/2024"}, value="review", word_ids=["w3"],
        event_date=None, status="verified",
    )  # fmt: skip
    async with harness(
        admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, client_for=None, storage=storage,
        expert=expert, case_id=case_id, ocr_store=None,
    ) as h:  # fmt: skip
        async with api_db.request(scope_for(expert)) as conn:
            for n, (word_id, text) in enumerate((("w1", "03/04/2024"), ("w2", "seen"), ("w3", "review")), start=1):
                sealed = await h.crypto.encrypt(
                    conn, case_id, text.encode(), purpose=paths.word_purpose(page_id, word_id)
                )
                admin.execute(
                    "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
                    " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, %s, 0.9, 'ocr')",
                    (page_id, case_id, n, word_id, sealed, [0, 0, 1, 0, 1, 1, 0, 1]),
                )
        assert (await h.client.post(f"/v1/cases/{case_id}/repair-fact-dates")).status_code == 202
        await h.drain()
        now = admin.execute("select event_date, word_ids from app.facts where id = %s", (fact_id,)).fetchone()
        assert now is not None and now[0] is not None, "control: the pipeline dated the checked fact"
        assert {"w3"} <= set(now[1]), "the fact's own words are kept; the date's words are added"
        rows = admin.execute(
            "select action, person_id, reason, old_word_ids, new_word_ids, old_event_date, new_event_date,"
            " old_date_basis, new_date_basis from app.fact_verifications where fact_id = %s order by seq",
            (fact_id,),
        ).fetchall()
        assert len(rows) == 1, "a checked fact's date and cited words changed with no row in the check log"
        action, person, reason, old_ids, new_ids, old_date, new_date, old_basis, new_basis = rows[0]
        assert (action, person, reason) == ("remapped", None, "date_repair")
        assert (list(old_ids), list(new_ids)) == (["w3"], list(now[1]))
        assert (old_date, new_date, old_basis, new_basis) == (None, now[0], None, "printed")
        ok = admin.execute("select app.fact_verifications_chain_ok(%s)", (case_id,)).fetchone()
        assert ok == (True,), "the check log chain no longer verifies"
        same = admin.execute(
            "select v.old_fields is not distinct from f.fields and v.new_fields is not distinct from f.fields"
            " and v.old_page_id = f.page_id and v.new_page_id = f.page_id and v.old_fields is not null"
            " from app.fact_verifications v join app.facts f on f.id = v.fact_id where v.fact_id = %s",
            (fact_id,),
        ).fetchone()
        assert same == (True,), "the date-repair row leaves the old side's details or page empty"


# --- PV-02b: every log row the pipeline writes carries the fact before and after --------------------------------------


async def test_PV02_a_quarantine_and_its_undo_log_the_checked_fact_with_old_and_new_values(start: Start) -> None:
    h = await start(Fake(with_facts))
    await process(h, text_pdf([["Alpha entry recorded today for review"]]))
    (fact_row,) = facts_on(h, 1)
    h.admin.execute(
        "update app.facts set status = 'verified', event_date = '2024-04-03', date_basis = 'printed' where id = %s",
        (fact_row["id"],),
    )
    assert (await h.client.post(quarantine_url(h, 1), json={"reason": "not_this_person"})).status_code == 200
    assert (await h.client.delete(quarantine_url(h, 1))).status_code == 200
    rows = h.admin.execute(
        "select v.old_value is not distinct from f.value and v.new_value is not distinct from f.value,"
        " v.old_fields is not distinct from f.fields and v.new_fields is not distinct from f.fields,"
        " v.old_word_ids is not distinct from f.word_ids and v.new_word_ids is not distinct from f.word_ids,"
        " v.old_page_id = f.page_id and v.new_page_id = f.page_id,"
        " v.old_event_date = f.event_date and v.new_event_date = f.event_date,"
        " v.old_date_basis = f.date_basis and v.new_date_basis = f.date_basis"
        " from app.fact_verifications v join app.facts f on f.id = v.fact_id where v.fact_id = %s order by v.seq",
        (fact_row["id"],),
    ).fetchall()
    assert len(rows) == 2, f"{len(rows)} log rows for a quarantine and its undo"
    for n, row in enumerate(rows):
        assert row == (True,) * 6, f"log row {n} has an empty or wrong old side: {row}"


# --- Set-aside: a replaced page is set aside wherever the pipeline counts pages --------------------------------------


async def test_a_replaced_page_is_not_queued_not_matched_not_in_gaps_and_not_planned(start: Start) -> None:
    from app.features.pipeline import repo

    h = await start(Fake())
    await process(h, text_pdf([LETTER, OTHER_VALUE, LETTER]))
    one, two, three = (page_id(h, n) for n in (1, 2, 3))
    h.admin.execute("update app.pages set superseded_by_page_id = %s where id = any(%s)", (three, [one, two]))
    h.admin.execute("update app.page_analysis set extracted_at = null where page_id = any(%s)", ([two, three],))
    h.admin.execute("update app.page_analysis set references_checked_at = null where page_id = %s", (one,))
    async with h.api_db.request(scope_for(h.expert)) as conn:
        queued = [n for _, n in await repo.pages_to_extract(conn, h.case_id)]
        matched = [n for _, n in await repo.pages_to_match(conn, h.case_id)]
        in_gaps = [p.page_no for p in await repo.gap_pages(conn, h.case_id)]
        compared = await repo.pages_for_identity(conn, h.case_id)
        planned = await repo.pages_to_plan(conn, h.case_id)
    assert queued == [3], f"pages {queued} are queued for extraction; a replaced page is set aside"
    assert matched == [], f"pages {matched} are queued for reference matching; a replaced page is set aside"
    assert in_gaps == [3], f"pages {in_gaps} feed gap detection; a replaced page is set aside"
    assert compared == [three], "a replaced page is compared for identity"
    assert planned == 1, f"{planned} pages are planned for a run; a replaced page is set aside"


def test_a_replaced_page_is_not_in_the_progress_total(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    parent = make_job(admin, seed, case)
    add_run(admin, case, parent, "sort_waiting")
    ids = pages(admin, seed, case, 10, read=10, extracted=4)
    add_batch(admin, parent, case, 0, phase="sort", pages=10)
    admin.execute("update app.pages set superseded_by_page_id = %s where id = %s", (ids[8], ids[9]))
    report(admin, case, parent)
    assert parent_row(admin, parent) == ("sort", 46, 4, 9), "a replaced page is counted in the progress total"


# --- The region of an OCR call is the cell's, never a placeholder ----------------------------------------------------


def test_the_region_of_a_recorded_call_is_the_cells_and_a_missing_one_is_a_configuration_error() -> None:
    import dataclasses

    from app.core.config import ConfigError
    from app.features.pipeline import service
    from tests.support.ai import cell

    settings = cell()
    kit = type("Kit", (), {"settings": settings})()
    services = type("Services", (), {"kit": kit})()
    assert service._region(services) == settings.region
    for broken in (dataclasses.replace(settings, region=""), None):
        kit = type("Kit", (), {"settings": broken})()
        services = type("Services", (), {"kit": kit})()
        with pytest.raises(ConfigError):
            service._region(services)
