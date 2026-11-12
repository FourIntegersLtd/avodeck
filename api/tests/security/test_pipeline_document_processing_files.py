"""Audit 13: hostile and broken files through the real split job (zip tricks, odd page trees, recovery mode, races,
log hygiene, retries). Everything is synthetic; a canary string stands in for patient text and must never be logged."""

from __future__ import annotations

import asyncio
import io
import logging
import uuid
import zipfile
from pathlib import Path
from typing import Any

import pikepdf
import pytest

from app.features.pipeline import convert, parsing, repo
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support import pipeline_parse_stand_ins as stand_in
from tests.support.file_lifecycle_documents import (
    _save,
    _typed_page,
    encrypted_pdf,
    text_pdf,
    truncated_pdf,
    upload_bytes,
    zip_of,
)

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage
all_documents, run = lifecycle.all_documents, lifecycle.run
CANARY = "CANARY-9d41-private"


# --- zips: overlapping and repeated entries ---------------------------------------------------------


def test_zip_entries_that_overlap_are_refused_or_stay_inside_the_budget() -> None:
    data = bytearray(zip_of({"a.bin": b"x" * 5000, "b.bin": b"y" * 5000}, deflate=False))
    first = data.find(b"PK\x01\x02")
    second = data.find(b"PK\x01\x02", first + 4)
    # entry b points at entry a's local header: two directory entries, one body (a quine-style overlap)
    data[second + 42 : second + 46] = data[first + 42 : first + 46]
    limits = convert.Limits(max_bytes=12_000, max_members=10)
    found = convert.unpack(bytes(data), "overlap.zip", limits)
    assert found.exhibit or sum(len(m.data) for m in found.members) <= limits.max_bytes


def test_a_zip_with_the_same_name_twice_unpacks_both_members_inside_the_budget() -> None:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as z:
        z.writestr("same.bin", b"one")
        with pytest.warns(UserWarning):
            z.writestr("same.bin", b"two")
    found = convert.unpack(out.getvalue(), "dupe.zip", convert.Limits())
    assert not found.exhibit and sorted(m.data for m in found.members) == [b"one", b"two"]


# --- DP-05: one recovery mode for the count and for the cut -----------------------------------------


async def test_every_open_of_a_stored_pdf_for_cutting_runs_without_recovery(
    h: Harness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The count and the cut now run in sandbox children, out of reach of a spy, so the spy watches the functions
    the children run; the split itself is run end to end as well."""
    opened: list[dict[str, Any]] = []
    real = pikepdf.open

    def spy(*args: Any, **kwargs: Any) -> pikepdf.Pdf:
        opened.append({"path": not isinstance(args[0], io.BytesIO), **kwargs})
        return real(*args, **kwargs)

    monkeypatch.setattr(pikepdf, "open", spy)
    stored = tmp_path / "plain.pdf"
    stored.write_bytes(text_pdf(3))
    assert parsing.count_pages(str(stored), 100) == 3
    assert len(parsing.cut_pages(str(stored), [0, 2])) == 2
    from_disk = [o for o in opened if o["path"]]
    assert from_disk, "premise: documents were opened from a file"
    assert all(o.get("attempt_recovery") is False for o in from_disk), from_disk
    await run(h, text_pdf(3), "plain.pdf")
    await run(h, zip_of({"in.pdf": text_pdf(1)}), "pack.zip")


# --- two splits at once never repeat or skip a bundle page number ------------------------------------


async def test_two_splits_of_one_case_at_the_same_time_number_their_pages_without_a_clash(h: Harness) -> None:
    first = await h.upload(_pdf(2), filename="one.pdf")
    second = await h.upload(_pdf(3), filename="two.pdf")
    for doc in (first, second):  # the scan job has not run: mark both clean, as it would
        h.admin.execute(
            "insert into app.document_scans (document_id, bundle_id, clean)"
            " values (%s, (select bundle_id from app.cases where id = %s), true) on conflict do nothing",
            (doc, h.case_id),
        )
    async with h.worker_db.job(h.case_id) as a, h.worker_db.job(h.case_id) as b:
        await repo.add_pages(a, h.case_id, first, 2)
        later = asyncio.ensure_future(repo.add_pages(b, h.case_id, second, 3))  # runs while `a` is still open
        await asyncio.sleep(0.5)
        await a.commit()
        await later
        await b.commit()
    numbers = [p["bundle_page_no"] for p in h.pages()]
    assert numbers == [1, 2, 3, 4, 5], numbers


def _pdf(pages: int) -> bytes:
    return text_pdf(pages)


# --- odd page trees and shapes ---------------------------------------------------------------------


def odd_pdf() -> bytes:
    """A text page, a page of no area and a page of 200,000 points a side, under a tree that declares 500,000 pages."""
    pdf = pikepdf.open(io.BytesIO(text_pdf(3)))
    pdf.pages[1].MediaBox = [0, 0, 0, 0]
    pdf.pages[2].MediaBox = [0, 0, 200_000, 200_000]
    pdf.Root.Pages.Count = 500_000
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


async def test_a_page_tree_that_declares_far_more_pages_than_it_has_and_odd_page_shapes_do_not_fail_the_job(
    h: Harness,
) -> None:
    doc = await run(h, odd_pdf(), "odd.pdf", allow_paused=True)
    shown = [p for p in h.pages() if str(p["document_id"]) == doc]
    state = lifecycle.state(h, doc)
    assert state["unreadable_reason"] == "damaged" or len(shown) == 3, "numbered as it is, or listed as damaged"
    if shown:
        assert all(p["image_path"] for p in shown), "every page got its picture"


# --- failures name codes only ------------------------------------------------------------------------


async def test_no_failure_path_writes_page_text_a_file_name_or_file_content_to_the_logs_or_a_job_row(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    bad_docx = b"PK\x03\x04" + CANARY.encode() * 50  # looks like a zip, is not one
    await run(h, encrypted_pdf(CANARY), f"{CANARY}-locked.pdf")
    await run(h, truncated_pdf(3, 0.4), f"{CANARY}-cut.pdf")
    await run(h, b"%PDF-1.7\n" + CANARY.encode() * 50, f"{CANARY}-garbage.pdf")
    await run(h, bad_docx, f"{CANARY}.docx")
    await run(h, zip_of({f"{CANARY}.bin": CANARY.encode()}), f"{CANARY}.zip")
    assert any(d["unreadable_reason"] for d in all_documents(h)), "premise: failures really happened"
    assert CANARY not in caplog.text
    rows = h.admin.execute("select row_to_json(j)::text from app.jobs j where j.case_id = %s", (h.case_id,)).fetchall()
    assert rows and not any(CANARY in r[0] for r in rows), "a job row carries text"
    reasons = [d["unreadable_reason"] for d in all_documents(h) if d["unreadable_reason"]]
    assert all(r.isidentifier() for r in reasons), reasons


# --- a retry after a crash adds nothing twice --------------------------------------------------------


async def test_a_retry_after_a_mid_job_crash_creates_no_duplicate_pages_or_words(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The crash is a drawing call that raises on the second page; it runs in a sandbox child, so the stand-in is
    a module-level function that raises on a marked page."""
    real = parsing.pictures
    marked = pikepdf.new()
    for words in ("keep one", f"{stand_in.MARK} two", "keep three"):
        _typed_page(marked, words)
    monkeypatch.setattr(parsing, "pictures", stand_in.pictures_crash)
    _, doc = await upload_bytes(h, _save(marked), filename="crash.pdf")
    first = await h.drain(timeout=90)
    assert "failed" in first.values(), "premise: the crash was reached"
    monkeypatch.setattr(parsing, "pictures", real)
    again = await h.enqueue("process_bundle")
    assert (await h.drain(timeout=90))[again] == "done"
    pages = [p for p in h.pages() if str(p["document_id"]) == doc]
    assert [p["bundle_page_no"] for p in pages] == [1, 2, 3] and all(p["image_path"] for p in pages)
    dupes = h.admin.execute(
        "select count(*) from (select w.page_id, w.n from app.ocr_words w join app.pages p on p.id = w.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) group by 1, 2 having count(*) > 1) d",
        (h.case_id,),
    ).fetchone()
    assert dupes is not None and dupes[0] == 0
    assert len(set(uuid.UUID(str(p["id"])) for p in pages)) == 3
