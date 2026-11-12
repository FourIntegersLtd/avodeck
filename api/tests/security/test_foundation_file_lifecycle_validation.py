"""File lifecycle audit, step 12: what the real split job does with hostile or broken documents (V3, V4).

Each test states the SAFE behaviour. Where the application is not safe, the safe assertion is kept and the test
is marked `xfail(strict=True)` with the finding id. Every "refused" assertion has a positive control: a plain file
of the same kind is accepted by the same code. Bomb tests use small `convert.Limits` and measure the bytes that
were really decompressed, and never allocate what the file merely claims.
"""

from __future__ import annotations

import io
import os
import tempfile
import zipfile
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from PIL import Image

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.pipeline import convert, parsing, render
from app.features.pipeline import service as pipeline_service
from app.features.pipeline.paths import page_pdf_path
from tests.repository.test_pipeline_support import Harness, empty_answer, harness
from tests.support import sandbox_fns
from tests.support.ai import ScriptedBedrock
from tests.support.file_lifecycle_documents import (
    ACTIVE_MARKERS,
    active_content_pdf,
    blank_pages_pdf,
    declared_size_zip,
    encrypted_pdf,
    forged_size_zip,
    garbage_after_pdf_marker,
    huge_mediabox_pdf,
    nested_zip,
    password_flagged_zip,
    pdf_then_zip,
    pdf_with_html_tail,
    png_declaring,
    png_with_zip_tail,
    real_image,
    sha,
    text_pdf,
    tiff_frames,
    truncated_pdf,
    upload_bytes,
    zip_in_email_in_zip,
    zip_of,
)
from tests.support.files import local_storage
from tests.support.packs import FIXTURE
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SMALL = convert.Limits(max_bytes=100_000, max_members=5, max_depth=3)


# --- fixtures and readers --------------------------------------------------------------------------


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def h(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=FIXTURE.case_row())
    first_source = default_library().combine(*FIXTURE.codes).source_types()[0]["key"]
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


def state(h: Harness, doc: str) -> dict[str, Any]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    row = cur.execute(
        "select unreadable_reason, pages, filename, storage_path from app.documents where id = %s", (doc,)
    ).fetchone()
    assert row is not None
    row["page_rows"] = len([p for p in h.pages() if str(p["document_id"]) == doc])
    row["exhibits"] = cur.execute("select count(*) as n from app.exhibits where document_id = %s", (doc,)).fetchone()[
        "n"
    ]  # type: ignore[index]
    return dict(row)


def all_documents(h: Harness) -> list[dict[str, Any]]:
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    return cur.execute(
        "select id, filename, storage_path, unreadable_reason from app.documents"
        " where bundle_id = (select bundle_id from app.cases where id = %s) order by created_at, id",
        (h.case_id,),
    ).fetchall()


def exhibit_bytes(h: Harness) -> int:
    row = h.admin.execute(
        "select coalesce(sum(size), 0) from app.exhibits where bundle_id = (select bundle_id from app.cases where id "
        "= %s)",
        (h.case_id,),
    ).fetchone()
    return int(row[0]) if row else 0


async def run(h: Harness, data: bytes, name: str, *, allow_paused: bool = False) -> str:
    """Upload, let the real jobs run, and return the document id. `allow_paused`: no OCR stand-in is wired here,
    so a page with no text layer pauses the job at OCR; the split itself has finished by then."""
    _, doc = await upload_bytes(h, data, filename=name)
    finished = await h.drain()
    allowed = {"done", "paused"} if allow_paused else {"done"}
    assert set(finished.values()) <= allowed, finished
    return doc


async def page_texts(h: Harness, doc: str) -> list[int]:
    """Length of the text layer of each stored page PDF of the document, in page order."""
    import pypdfium2 as pdfium

    sizes = []
    for page in [p for p in h.pages() if str(p["document_id"]) == doc]:
        path = page_pdf_path(h.case_id, page["id"])
        stored = await h.decrypt(await h.storage.get(path), path)
        sizes.append(len(pdfium.PdfDocument(stored)[0].get_textpage().get_text_range()))
    return sizes


# --- V3: archives, with small limits and measured bytes ---------------------------------------------


def test_v3_the_member_count_limit_refuses_one_over_and_accepts_the_limit() -> None:
    at = convert.unpack(zip_of({f"m{i}.txt": b"x" for i in range(SMALL.max_members)}), "a.zip", SMALL)
    assert not at.exhibit and len(at.members) == SMALL.max_members, "control: the limit itself is accepted"
    over = convert.unpack(zip_of({f"m{i}.txt": b"x" for i in range(SMALL.max_members + 1)}), "a.zip", SMALL)
    assert over.exhibit and over.members == []


def test_v3_an_honest_bomb_is_refused_before_a_single_byte_is_inflated(monkeypatch: pytest.MonkeyPatch) -> None:
    inflated = 0
    real_read = zipfile.ZipFile.read

    def counting(self: zipfile.ZipFile, name: Any, pwd: Any = None) -> bytes:
        nonlocal inflated
        out = real_read(self, name, pwd)
        inflated += len(out)
        return out

    monkeypatch.setattr(zipfile.ZipFile, "read", counting)
    fine = convert.unpack(zip_of({"a.bin": b"\0" * 90_000}), "a.zip", SMALL)
    assert not fine.exhibit and inflated == 90_000, "control: a member within the limit is inflated in full"

    inflated = 0
    bomb = zip_of({"b.bin": b"\0" * 30_000_000})
    assert len(bomb) < 100_000, "premise: tiny on the wire, huge when inflated"
    refused = convert.unpack(bomb, "b.zip", SMALL)
    assert refused.exhibit and refused.members == [] and inflated == 0


@pytest.mark.parametrize("honest_crc", [False, True], ids=["bad-crc", "crc-of-the-prefix"])
def test_v3_a_zip_that_lies_about_its_size_never_inflates_past_what_it_declared(
    monkeypatch: pytest.MonkeyPatch, honest_crc: bool
) -> None:
    got = 0
    real_read = zipfile.ZipFile.read

    def counting(self: zipfile.ZipFile, name: Any, pwd: Any = None) -> bytes:
        nonlocal got
        out = real_read(self, name, pwd)
        got += len(out)
        return out

    monkeypatch.setattr(zipfile.ZipFile, "read", counting)
    real, declared = 30_000_000, 10
    liar = forged_size_zip(real, declared, honest_crc_of_prefix=honest_crc)
    found = convert.unpack(liar, "liar.zip", SMALL)
    returned = sum(len(m.data) for m in found.members)
    assert returned <= declared and got <= declared, f"{got} bytes were inflated for a declared {declared}"
    assert got < real
    if not honest_crc:
        assert found.exhibit and found.members == [], "a size that does not match its CRC is refused"
    # control: the same forged-size path with an honest header returns the whole member
    honest = convert.unpack(zip_of({"honest.bin": b"\0" * 50_000}), "h.zip", SMALL)
    assert [len(m.data) for m in honest.members] == [50_000]


def test_v3_a_declared_size_below_the_real_one_does_not_hide_a_larger_member() -> None:
    claimed = declared_size_zip(b"\0" * 400_000, 50_000)  # real 400 kB, header says 50 kB, limit is 100 kB
    found = convert.unpack(claimed, "claimed.zip", SMALL)
    assert sum(len(m.data) for m in found.members) <= SMALL.max_bytes


def test_v3_a_zip_marked_as_password_locked_is_kept_as_an_exhibit_not_unpacked() -> None:
    locked = convert.unpack(password_flagged_zip({"a.pdf": text_pdf(1)}), "locked.zip", SMALL)
    assert locked.exhibit and locked.members == []
    assert len(convert.unpack(zip_of({"a.pdf": text_pdf(1)}), "open.zip", SMALL).members) == 1, "control"


async def test_v3_nesting_stops_at_the_depth_limit_and_the_rest_is_kept_whole(h: Harness) -> None:
    ok = await run(h, nested_zip(3, text_pdf(1, "inner")), "three.zip")
    assert len(h.pages()) == 1, "control: a leaf at the limit becomes a page"
    deep = await run(h, nested_zip(6, text_pdf(1, "too-deep")), "six.zip")
    assert len(h.pages()) == 1, "nothing below the limit became a page"
    assert state(h, ok)["page_rows"] == 0
    kept = [
        d
        for d in all_documents(h)
        if h.admin.execute("select 1 from app.exhibits where document_id = %s", (d["id"],)).fetchone()
    ]
    assert kept, "the level that was not unpacked is kept as an exhibit, so the file is not silently lost"
    assert state(h, deep)["pages"] in (0, None)


async def test_v3_a_zip_holding_an_email_holding_a_zip_is_followed_within_the_limit(h: Harness) -> None:
    await run(h, zip_in_email_in_zip(text_pdf(1, "leaf")), "chain.zip")
    assert len(h.pages()) >= 2, "control: the email body page and the leaf page both exist"


async def test_v3_one_upload_may_not_unpack_to_more_than_the_limit_in_total(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_service, "LIMITS", SMALL)
    await run(h, zip_of({"only.zip": zip_of({"z.bin": b"\0" * 90_000})}), "control.zip")
    assert exhibit_bytes(h) == 90_000, "control: a single 90 kB member is unpacked"
    before = exhibit_bytes(h)
    fan = zip_of({f"z{i}.zip": zip_of({f"z{i}.bin": bytes([i]) * 90_000}) for i in range(5)})
    await run(h, fan, "fan.zip")
    assert exhibit_bytes(h) - before <= SMALL.max_bytes, f"{exhibit_bytes(h) - before} bytes came out of one upload"


async def test_v3_member_names_never_reach_a_storage_path_and_every_member_is_numbered(h: Harness) -> None:
    members = {
        "../../evil-one.pdf": text_pdf(1, "one"),
        "/abs/evil-two.pdf": text_pdf(1, "two"),
        "..\\win\\evil-three.pdf": text_pdf(1, "three"),
        "fine.pdf": text_pdf(1, "four"),
    }
    await run(h, zip_of(members), "names.zip")
    assert len(h.pages()) == 4, "control: every member was unpacked and numbered"
    for d in all_documents(h):
        path = d["storage_path"]
        assert path is not None, f"a document with no stored path: {d['filename']!r}"
        assert path.startswith(f"cases/{h.case_id}/") and ".." not in path and "evil" not in path, path


async def test_v3_a_member_name_follows_the_same_filename_rule_as_an_upload(h: Harness) -> None:
    body = {"kind": "records", "filename": "bad\nname.pdf", "size": 10, "sha256": sha(b"0" * 10), "append": False}
    refused = await h.client.post(f"/v1/cases/{h.case_id}/uploads", json=body)
    assert refused.status_code == 422, "control: the upload route refuses a control character in a name"
    await run(h, zip_of({"bad\nname.pdf": text_pdf(1), "good.pdf": text_pdf(1, "g")}), "named.zip")
    names = [d["filename"] for d in all_documents(h)]
    assert "good.pdf" in names, "control: the clean member name is kept"
    assert not any(any(ord(c) < 32 or ord(c) == 127 for c in n) for n in names)


# --- V4: broken and hostile documents through the real split job ------------------------------------


async def test_v4_control_an_intact_pdf_is_numbered_and_not_marked_unopened(h: Harness) -> None:
    doc = await run(h, text_pdf(3), "intact.pdf")
    s = state(h, doc)
    assert s["unreadable_reason"] is None and s["page_rows"] == 3 and s["pages"] == 3


async def test_v4_a_locked_pdf_is_listed_as_not_opened_and_does_not_stop_the_others(h: Harness) -> None:
    locked = await run(h, encrypted_pdf(), "locked.pdf")
    good = await run(h, text_pdf(2, "after"), "good.pdf")
    assert state(h, locked)["unreadable_reason"] == "locked" and state(h, locked)["page_rows"] == 0
    assert state(h, good)["page_rows"] == 2, "control: the next file is still numbered"
    summary = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert summary.status_code == 200 and summary.json()["unopened_count"] == 1


@pytest.mark.parametrize(("keep", "surviving"), [pytest.param(0.3, 2), pytest.param(0.45, 3)])
async def test_v4_a_cut_off_pdf_is_marked_damaged_or_keeps_every_page_with_its_content(
    h: Harness, keep: float, surviving: int
) -> None:
    intact = await run(h, truncated_pdf(3, keep=1.0, surviving=3), "whole.pdf")
    assert state(h, intact)["page_rows"] == 3 and await page_texts(h, intact) == [16, 16, 16], "control"
    # `surviving` is checked against the repairing reader when the file is built, so the 3-page claim is a measured one
    cut = await run(h, truncated_pdf(3, keep=keep, surviving=surviving), "cut.pdf", allow_paused=True)
    s = state(h, cut)
    if s["unreadable_reason"] == "damaged":
        assert s["page_rows"] == 0
    else:
        assert s["page_rows"] == 3 and all(n > 0 for n in await page_texts(h, cut)), s


async def test_v4_a_pdf_marker_followed_by_garbage_is_marked_damaged_and_does_not_fail_the_job(h: Harness) -> None:
    doc = await run(h, garbage_after_pdf_marker(), "garbage.pdf")
    s = state(h, doc)
    assert s["page_rows"] == 0 and (s["unreadable_reason"] is not None or s["exhibits"] == 1), s
    good = await run(h, text_pdf(1, "next"), "next.pdf")
    assert state(h, good)["page_rows"] == 1, "control: the pipeline carries on"


async def test_v4_a_program_called_pdf_is_kept_as_an_exhibit_and_never_paged(h: Harness) -> None:
    doc = await run(h, b"MZ" + bytes(range(256)) * 20, "report.pdf")
    s = state(h, doc)
    assert s["page_rows"] == 0 and s["exhibits"] == 1 and s["unreadable_reason"] is None, s
    ok = await run(h, text_pdf(1, "pdf"), "real.pdf")
    assert state(h, ok)["exhibits"] == 0 and state(h, ok)["page_rows"] == 1, "control: a real PDF is paged"


async def test_v4_a_pdf_with_no_pages_is_not_accepted_silently(h: Harness) -> None:
    import pikepdf

    out = io.BytesIO()
    pikepdf.new().save(out)
    doc = await run(h, out.getvalue(), "empty.pdf")
    s = state(h, doc)
    assert s["page_rows"] == 0
    assert s["unreadable_reason"] is not None or s["exhibits"] == 1, (
        f"no sign for the expert that it gave no pages: {s}"
    )


async def test_v4_a_file_that_is_a_pdf_and_a_zip_is_read_as_the_pdf_it_starts_as(h: Harness) -> None:
    doc = await run(h, pdf_then_zip(), "both.pdf")
    s = state(h, doc)
    assert s["page_rows"] == 1 and s["exhibits"] == 0
    assert len(all_documents(h)) == 1, "the zip hidden after the PDF did not become documents"


async def test_v4_the_original_upload_is_kept_exactly_as_received(h: Harness) -> None:
    data = active_content_pdf()
    doc = await run(h, data, "active.pdf")
    got = await h.client.get(f"/v1/cases/{h.case_id}/documents/{doc}/file")
    assert got.status_code == 200 and sha(got.content) == sha(data), "evidence is kept byte for byte (by design)"
    assert all(m in data for m in (b"/JavaScript", b"/OpenAction", b"/Launch", b"/GoToR", b"/EmbeddedFile")), (
        "premise: the fixture really carries active content"
    )


# Routes checked by reading for V4a: no route serves a stored page PDF to a browser. `page_pdf_path` is used by the
# split (pipeline/router.py) and the OCR re-read (reader/second_reading.py) only; the other PDFs the API sends (trial
# pack, certificate of deletion, invoices, report, transcript) are different artefacts, and the trial pack and the
# certificate carry `Content-Disposition: attachment`. So this is information, not an exploitable path today.
@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V4a info: the one-page PDF stored for each page keeps the page's own /AA JavaScript and its "
    "/Launch, /URI and /GoToR link actions (only document-level /OpenAction, name trees and embedded files are "
    "dropped); no route serves a stored page PDF to a browser today (checked by reading), so it matters only if one "
    "is added: app/core/pdf.py:39-47 (cut_page copies the page object), app/features/pipeline/service.py:320",
)
async def test_v4_a_stored_page_carries_no_script_launch_or_outward_link(h: Harness) -> None:
    doc = await run(h, active_content_pdf(), "active.pdf")
    pages = [p for p in h.pages() if str(p["document_id"]) == doc]
    assert len(pages) == 1, "control: the page was made"
    path = page_pdf_path(h.case_id, pages[0]["id"])
    stored = await h.decrypt(await h.storage.get(path), path)
    assert stored.startswith(b"%PDF-"), "control: the stored page is the PDF we looked for"
    found = [m.decode() for m in (*ACTIVE_MARKERS, b"/URI") if m in stored]
    assert found == [], f"stored page still carries {found}"


def test_v4_a_pdf_over_the_stated_page_limit_is_refused() -> None:
    assert pipeline_service._checked_pages(blank_pages_pdf(10)) == 10, "control: a small PDF is counted"
    with pytest.raises(Exception):  # noqa: B017, PT011 - any refusal will do
        pipeline_service._checked_pages(blank_pages_pdf(4001))


def test_v4_an_image_over_the_stated_page_limit_is_refused() -> None:
    few = convert.unpack(tiff_frames(3), "a.tiff", SMALL)
    assert few.pdf is not None and not few.exhibit, "control: a small multi-frame image is paged"
    many = convert.unpack(tiff_frames(4001), "b.tiff")
    assert many.exhibit or many.pdf is None


def test_v4_an_image_that_claims_billions_of_pixels_is_kept_whole_not_decoded() -> None:
    bomb = convert.unpack(png_declaring(60_000, 60_000), "bomb.png")
    assert bomb.exhibit and bomb.pdf is None
    ok = convert.unpack(real_image("PNG", (16, 16)), "ok.png")
    assert ok.pdf is not None and not ok.exhibit, "control: a real picture becomes a page"
    assert Image.MAX_IMAGE_PIXELS is not None


class _FakeBitmap:
    def to_pil(self) -> Image.Image:
        return Image.new("RGB", (2, 2))


def test_v4_a_page_picture_is_never_asked_for_more_pixels_than_the_image_library_allows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import pypdfium2 as pdfium

    asked: list[int] = []

    def recording(self: Any, *, scale: float = 1, rotation: int = 0, **_: Any) -> _FakeBitmap:
        width, height = self.get_size()
        asked.append(int(width * scale) * int(height * scale))
        return _FakeBitmap()

    monkeypatch.setattr(pdfium.PdfPage, "render", recording)
    render.rasterise(text_pdf(1))
    assert 1_000_000 < asked[-1] < 5_000_000, f"control: an A4 page is about 2 million pixels, asked {asked[-1]}"
    render.rasterise(huge_mediabox_pdf(14400))
    limit = Image.MAX_IMAGE_PIXELS
    assert limit is not None, "the image library's pixel limit is switched off"
    assert asked[-1] <= limit, f"asked pdfium for {asked[-1]:,} pixels"


# --- how the original is served, polyglots, image bombs and temp files ------------------------------------


async def _served(h: Harness, doc: str) -> Any:
    got = await h.client.get(f"/v1/cases/{h.case_id}/documents/{doc}/file")
    assert got.status_code == 200, got.text
    return got


async def test_v4_the_original_is_served_as_opaque_bytes_that_a_browser_will_not_sniff_or_cache(h: Harness) -> None:
    doc = await run(h, active_content_pdf(), "active.pdf")
    got = await _served(h, doc)
    assert got.headers["content-type"] == "application/octet-stream", "never as a PDF or a page a browser would run"
    assert got.headers["x-content-type-options"] == "nosniff"
    assert got.headers["cache-control"] == "private, no-store"


async def test_s3_the_original_is_sent_as_an_attachment(h: Harness) -> None:
    doc = await run(h, text_pdf(1), "plain.pdf")
    got = await _served(h, doc)
    assert got.headers["content-type"] == "application/octet-stream", "control: the rest of the answer is as expected"
    assert got.headers.get("content-disposition", "").startswith("attachment"), dict(got.headers)


def _media_type(h: Harness, doc: str) -> str | None:
    row = h.admin.execute("select media_type from app.document_scans where document_id = %s", (doc,)).fetchone()
    return None if row is None else str(row[0])


@pytest.mark.parametrize(
    ("data", "sniffed", "pages"),
    [
        pytest.param(pdf_with_html_tail(), convert.PDF, 1, id="pdf-and-html"),
        pytest.param(png_with_zip_tail(), "image/png", 1, id="png-and-zip"),
    ],
)
async def test_v4_a_file_that_is_two_formats_is_read_as_its_first_stored_whole_and_served_as_opaque_bytes(
    h: Harness, data: bytes, sniffed: str, pages: int
) -> None:
    assert convert.sniff(data) == sniffed, "premise: the first bytes decide what it is"
    doc = await run(h, data, "polyglot.bin", allow_paused=True)
    s = state(h, doc)
    assert s["page_rows"] == pages and s["exhibits"] == 0, s
    assert len(all_documents(h)) == 1, "whatever is hidden after the first format did not become a document"
    assert _media_type(h, doc) == sniffed
    got = await _served(h, doc)
    assert sha(got.content) == sha(data), "stored byte for byte"
    assert got.headers["content-type"] == "application/octet-stream" and got.headers["x-content-type-options"] == (
        "nosniff"
    )


def test_v4_the_image_reader_refuses_a_picture_that_claims_billions_of_pixels_before_allocating_it() -> None:
    fine = convert._image_pages(real_image("PNG", (16, 16)))
    assert fine.startswith(b"%PDF-"), "control: a real picture becomes a one-page PDF"
    with pytest.raises(Image.DecompressionBombError):
        convert._image_pages(png_declaring(60_000, 60_000))  # claims 3.6 billion pixels in a few dozen bytes


def test_v4_the_pixel_limit_is_what_stops_the_image_reader(monkeypatch: pytest.MonkeyPatch) -> None:
    picture = real_image("PNG", (100, 100))  # 10,000 pixels
    assert convert._image_pages(picture).startswith(b"%PDF-"), "control: accepted under the normal limit"
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1_000)
    with pytest.raises(Image.DecompressionBombError):
        convert._image_pages(picture)


def _split_temp_names() -> set[str]:
    return {n for n in os.listdir(tempfile.gettempdir()) if n.startswith("avodeck-split-")}


async def test_v4_a_split_that_succeeds_leaves_no_temp_file_behind(h: Harness) -> None:
    before = _split_temp_names()
    await run(h, text_pdf(2), "plain.pdf")  # downloaded, counted, cut
    await run(h, zip_of({"a.pdf": text_pdf(1, "zip")}), "bundle.zip")
    # last: a picture has no text layer, so the case then waits at OCR (no OCR stand-in is wired here) and the next
    # upload could not run; its split, with the conversion and the second temp file, has finished by then
    await run(h, real_image("PNG", (16, 16)), "picture.png", allow_paused=True)
    assert len(h.pages()) >= 4, "control: every kind of file was really split"
    assert _split_temp_names() - before == set()


@pytest.mark.parametrize("failing", ["count", "cut"])
async def test_v4_a_split_that_fails_part_way_leaves_no_temp_file_behind(
    h: Harness, monkeypatch: pytest.MonkeyPatch, failing: str
) -> None:
    # The count and the cut run in sandbox children (DP-09), so the failure is a stand-in the child imports by name
    # ("count": after the download, while the document is counted; "cut": while a page is cut)
    monkeypatch.setattr(parsing, "count_pages" if failing == "count" else "cut_pages", sandbox_fns.raise_always)
    before = _split_temp_names()
    _, doc = await upload_bytes(h, text_pdf(2), filename="failing.pdf")
    finished = await h.drain(timeout=60)
    assert "failed" in finished.values(), finished  # premise: the failure was really reached
    assert state(h, doc)["page_rows"] in (0, 2)
    assert _split_temp_names() - before == set()
