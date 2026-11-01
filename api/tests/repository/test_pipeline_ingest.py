"""Slice A increment 1: an uploaded file is scanned, split and numbered (architecture 7 step 1).

Finalising an upload queues `scan_document` in the transaction that registers the document. An infected file is
flagged and audited and never becomes pages. A clean file goes on to `process_bundle`: every page
gets a permanent `bundle_page_no`, 1..N in upload order across documents, and a page image
(150-200 dpi) and thumbnail sealed under their own purpose. Running the job again changes nothing.
Runs under the launch and the fixture packs; the files are made up here.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from PIL import Image

from app.core.crypto import CiphertextInvalid
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import Harness, eicar, empty_answer, harness, text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

A4_WIDTH_INCHES = 595.0 / 72
WORDS_A = [["Synthetic alpha page one", "made up words"], ["Synthetic alpha page two"], ["Synthetic alpha three"]]
WORDS_B = [["Synthetic beta page one"], ["Synthetic beta page two"]]
MARKERS = ("alpha", "beta", "Synthetic")


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


def kinds(h: Harness) -> list[str]:
    return [
        r[0] for r in h.admin.execute("select kind from app.jobs where case_id = %s order by created_at", (h.case_id,))
    ]


async def test_completing_an_upload_queues_a_scan_in_the_same_transaction(h: Harness) -> None:
    await h.upload(text_pdf(WORDS_A))
    assert kinds(h) == ["finalize_upload", "scan_document"], "the scan is queued with the document, by the job"


async def test_a_clean_file_is_split_and_numbered_in_upload_order_across_documents(h: Harness) -> None:
    first = await h.upload(text_pdf(WORDS_A), filename="a.pdf")
    second = await h.upload(text_pdf(WORDS_B), filename="b.pdf")
    finished = await h.drain()
    assert set(finished.values()) == {"done"}
    assert "process_bundle" in kinds(h)
    pages = h.pages()
    assert [p["bundle_page_no"] for p in pages] == [1, 2, 3, 4, 5]
    assert [p["document_id"] for p in pages] == [first, first, first, second, second]
    scans = h.admin.execute(
        "select document_id, clean from app.document_scans where bundle_id = (select bundle_id from app.cases where id = %s) order by scanned_at", (h.case_id,)
    ).fetchall()
    assert sorted(scans) == sorted([(first, True), (second, True)])
    counts = h.admin.execute("select id, pages from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", (h.case_id,)).fetchall()
    assert dict(counts) == {first: 3, second: 2}


async def test_an_infected_file_is_flagged_audited_and_never_becomes_pages(h: Harness) -> None:
    bad = await h.upload(eicar(), filename="bad.pdf")
    await h.drain()
    assert h.pages() == []
    assert "process_bundle" not in kinds(h)
    row = h.admin.execute("select clean, signature from app.document_scans where document_id = %s", (bad,)).fetchone()
    assert row is not None and row[0] is False and row[1]
    audited = h.admin.execute(
        "select action from app.audit_log where case_id = %s and target = %s", (h.case_id, f"document:{bad}")
    ).fetchall()
    assert ("document.infected",) in audited


async def test_a_clean_file_after_an_infected_one_is_still_numbered_from_one(h: Harness) -> None:
    await h.upload(eicar(), filename="bad.pdf")
    good = await h.upload(text_pdf(WORDS_B), filename="b.pdf")
    await h.drain()
    assert [(p["bundle_page_no"], p["document_id"]) for p in h.pages()] == [(1, good), (2, good)]


async def test_each_page_image_and_thumbnail_is_sealed_under_its_own_purpose(h: Harness) -> None:
    from app.features.pipeline.paths import thumbnail_path

    await h.upload(text_pdf(WORDS_A))
    await h.drain()
    for page in h.pages():
        image_path = page["image_path"]
        assert image_path and str(h.case_id) in image_path, f"page {page['bundle_page_no']}"
        sealed = await h.storage.get(image_path)
        with pytest.raises(Exception):  # noqa: B017 - sealed bytes are not an image
            Image.open(io.BytesIO(sealed)).verify()
        image = Image.open(io.BytesIO(await h.decrypt(sealed, image_path)))
        dpi = image.width / A4_WIDTH_INCHES
        assert 150 <= round(dpi) <= 200, f"page {page['bundle_page_no']}"
        thumb_path = thumbnail_path(h.case_id, page["id"])
        thumb = Image.open(io.BytesIO(await h.decrypt(await h.storage.get(thumb_path), thumb_path)))
        assert thumb.width < image.width / 3, f"page {page['bundle_page_no']}"
        with pytest.raises(CiphertextInvalid):
            await h.decrypt(sealed, thumb_path)


async def test_running_the_job_again_changes_nothing(h: Harness) -> None:
    await h.upload(text_pdf(WORDS_A))
    await h.upload(text_pdf(WORDS_B))
    await h.drain()

    def snapshot() -> tuple[object, ...]:
        pages = [(p["id"], p["bundle_page_no"], p["image_path"], p["text_source"]) for p in h.pages()]
        words = h.admin.execute("select count(*) from app.ocr_words where bundle_id = (select bundle_id from app.cases where id = %s)", (h.case_id,)).fetchone()
        facts = h.admin.execute("select count(*) from app.facts where case_id = %s", (h.case_id,)).fetchone()
        return (pages, words, facts)

    before = snapshot()
    again = await h.enqueue("process_bundle")
    rescan = await h.enqueue("scan_document")
    finished = await h.drain()
    assert finished[again] == "done" and finished[rescan] == "done"
    assert snapshot() == before


async def test_progress_reaches_100_through_the_named_steps_with_no_page_text(
    h: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("DEBUG")
    await h.upload(text_pdf(WORDS_A))
    await h.drain()
    [job] = h.admin.execute(
        "select id, pct, row_to_json(j)::text from app.jobs j where case_id = %s and kind = 'process_bundle'",
        (h.case_id,),
    ).fetchall()
    assert job[1] == 100
    steps = {r[0] for r in h.admin.execute("select step from app.job_steps where job_id = %s", (job[0],))}
    assert {"split", "ocr", "sort", "duplicates", "missing"} <= steps
    for marker in MARKERS:
        assert marker not in job[2], "a job row holds page text"
        assert marker not in caplog.text, "a log line holds page text"


async def test_a_page_number_is_never_given_twice_even_when_two_files_arrive_together(h: Harness) -> None:
    ids = [await h.upload(text_pdf(WORDS_A), filename=f"{n}.pdf") for n in range(3)]
    await h.drain()
    numbers = [p["bundle_page_no"] for p in h.pages()]
    assert numbers == list(range(1, 10))
    assert [p["document_id"] for p in h.pages()] == [d for d in ids for _ in range(3)]
    assert len({uuid.UUID(str(p["id"])) for p in h.pages()}) == 9
