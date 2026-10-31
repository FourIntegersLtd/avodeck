"""Slice B, T-10 second reading of a page we couldn't read (architecture 5, 6.3; ADR 0030).

- `POST /v1/cases/{id}/pages/{n}/second-reading[?on_request=true]` (202 `{job_id}`, `page.transcribe`): 409 until the
  expert has typed the page out, unless `on_request=true`. The job masks identifiers on the page picture, asks the
  model to copy the handwriting and stores the lines sealed. A viewer gets 403; a stranger 404.
- `GET` the same path: `{status, reason, lines}`; `refused` with a reason code when masking was not safe, and then
  nothing was sent to the model.
- The model's lines never replace the expert's text and never become facts (rules 1 and 2).
- Rule 7: the picture the model was sent is black over every identifier box.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from PIL import Image

from app.core.ai.bedrock import ServiceBusy
from app.core.ai.kit import AiKit
from app.core.ai.ocr import OcrPage, OcrResult, OcrWord
from app.core.contracts.document_words import page_pdf_path, word_purpose
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.reader import second_reading
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.files import build_app, local_storage
from tests.support.identity import NAME, leaked, record_identity
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

DPI = 150
LINES = {"lines": [{"text": "Seen today chest clear", "confidence": 0.7}]}


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def model() -> ScriptedBedrock:
    return ScriptedBedrock([response(LINES, 800, 20)])


@pytest.fixture(autouse=True)
def services(
    monkeypatch: pytest.MonkeyPatch, storage: CaseFileStorage, crypto: CaseCrypto, model: ScriptedBedrock
) -> None:
    monkeypatch.setattr(second_reading.services, "storage", storage)
    monkeypatch.setattr(second_reading.services, "crypto", crypto)
    monkeypatch.setattr(second_reading.services, "kit", AiKit(cell(), model))
    monkeypatch.setattr(second_reading.services, "packs", default_library())
    monkeypatch.setattr(second_reading.services, "ocr", None)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage, crypto: CaseCrypto) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=crypto)


@pytest.fixture
async def aconn(admin_dsn: str) -> Any:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


def box(x: float, text: str) -> tuple[float, ...]:
    w = 0.12 * len(text)
    return (x, 0.5, x + w, 0.5, x + w, 0.7, x, 0.7)


async def make_page(
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    case: uuid.UUID,
    texts: list[str],
    *,
    confidence: float = 0.9,
    source: str = "ocr",
    typed: bool = False,
) -> uuid.UUID:
    doc, page = uuid.uuid4(), uuid.uuid4()
    await aconn.execute(
        "insert into app.documents (id, case_id, kind, filename, sha256) values (%s, %s, 'records', 'f.pdf', %s)",
        (doc, case, "0" * 64),
    )
    path = f"cases/{case}/pages/{page}/image.jpg"
    image = io.BytesIO()
    Image.new("RGB", (4 * DPI, 2 * DPI), "white").save(image, format="JPEG")
    await storage.put(path, await crypto.encrypt(aconn, case, image.getvalue(), purpose=path))
    pdf = page_pdf_path(case, page)
    await storage.put(pdf, await crypto.encrypt(aconn, case, b"%PDF stand-in", purpose=pdf))
    await aconn.execute(
        "insert into app.pages (id, case_id, document_id, bundle_page_no, image_path, handwriting, unreadable,"
        " transcribed_by_expert_text) values (%s, %s, %s, 1, %s, true, true, %s)",
        (page, case, doc, path, b"sealed" if typed else None),
    )
    await aconn.execute(
        "insert into app.page_analysis (page_id, case_id, width, height) values (%s, %s, 4.0, 2.0)", (page, case)
    )
    x = 0.1
    for n, text in enumerate(texts, start=1):
        wid = f"p1w{n}" if source == "ocr" else f"e{n}"
        polygon = list(box(x, text)) if source == "ocr" else [0.0] * 8
        sealed = await crypto.encrypt(aconn, case, text.encode(), purpose=word_purpose(page, wid))
        await aconn.execute(
            "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
            " values (%s, %s, %s, %s, %s, %s::real[], %s, %s)",
            (page, case, n, wid, sealed, polygon, confidence, source),
        )
        x += 0.12 * len(text) + 0.15
    return page


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, viewer, stranger = seed.person(), seed.person(), seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row(), status="active")
    seed.case_member(case, viewer, "viewer", added_by=expert)
    seed.case(seed.org(members={stranger: "owner"}), stranger, status="active")
    return {"case": case, "expert": expert, "viewer": viewer, "stranger": stranger}


def url(w: dict[str, Any], tail: str = "") -> str:
    return f"/v1/cases/{w['case']}/pages/1/second-reading{tail}"


async def run(worker_db: Database, admin: psycopg.Connection, w: dict[str, Any], job: str) -> None:
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    await second_reading.second_reading(JobContext(worker_db, uuid.UUID(job), w["case"], "second_reading", 1))


async def test_asking_before_the_page_is_typed_out_is_a_conflict_unless_on_request(
    client_for: Any,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    client = await client_for(world["expert"])
    assert (await client.post(url(world))).status_code == 409
    resp = await client.post(url(world, "?on_request=true"))
    assert resp.status_code == 202 and "job_id" in resp.json()


async def test_a_typed_out_page_can_be_read_again_without_asking_twice(
    client_for: Any,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"], typed=True)
    client = await client_for(world["expert"])
    assert (await client.post(url(world))).status_code == 202


async def test_a_viewer_is_forbidden_and_a_stranger_finds_nothing(
    client_for: Any,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    viewer = await client_for(world["viewer"])
    assert (await viewer.post(url(world, "?on_request=true"))).status_code == 403
    assert (await viewer.get(url(world))).status_code == 403
    stranger = await client_for(world["stranger"])
    assert (await stranger.post(url(world, "?on_request=true"))).status_code == 404
    assert (await stranger.get(url(world))).status_code == 404


async def test_the_masked_picture_is_read_and_the_lines_come_back_without_touching_the_record(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    await record_identity(aconn, crypto, world["case"])
    first, last = NAME.split()
    page = await make_page(aconn, storage, crypto, world["case"], ["Seen", first, last, "chest", "clear"])
    client = await client_for(world["expert"])
    assert (await client.get(url(world))).json() == {"status": "none", "reason": None, "lines": []}
    job = (await client.post(url(world, "?on_request=true"))).json()["job_id"]
    assert (await client.get(url(world))).json()["status"] == "queued"
    before = admin.execute("select count(*) from app.facts where case_id = %s", (world["case"],)).fetchone()
    await run(worker_db, admin, world, job)

    body = (await client.get(url(world))).json()
    assert body["status"] == "done" and body["lines"] == LINES["lines"]

    [sent] = model.requests  # rule 7: black over every identifier box, hash only in the record
    assert sent.image is not None and sent.image.masked_words == 2
    decoded = Image.open(io.BytesIO(sent.image.data)).convert("RGB")
    for left, top, right, bottom in sent.image.boxes:
        assert all(decoded.getpixel((x, y)) == (0, 0, 0) for x in range(left, right) for y in range(top, bottom))
    assert leaked([sent.text]) == []

    stored = admin.execute("select lines, status from app.second_readings where page_id = %s", (page,)).fetchone()
    assert stored is not None and b"chest" not in bytes(stored[0]), "the lines are sealed"
    after = admin.execute("select count(*) from app.facts where case_id = %s", (world["case"],)).fetchone()
    assert before == after
    text = admin.execute("select transcribed_by_expert_text from app.pages where id = %s", (page,)).fetchone()
    assert text == (None,), "the expert's text is never replaced"
    calls = admin.execute("select task_id, outcome from app.ai_calls where case_id = %s", (world["case"],)).fetchall()
    assert calls == [("T-10", "ok")]


async def test_an_unsafe_mask_refuses_and_nothing_is_sent(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    await record_identity(aconn, crypto, world["case"])
    first, last = NAME.split()
    await make_page(aconn, storage, crypto, world["case"], ["Seen", first, last], confidence=0.1)
    client = await client_for(world["expert"])
    job = (await client.post(url(world, "?on_request=true"))).json()["job_id"]
    await run(worker_db, admin, world, job)
    body = (await client.get(url(world))).json()
    assert body == {"status": "refused", "reason": "low_confidence", "lines": []}
    assert model.requests == []


class FreshOcr:
    """Boxes for the page as it is on the picture, because the stored words are the expert's own typing."""

    async def analyse(self, document: bytes, model: str) -> OcrResult:
        first, last = NAME.split()
        texts = ["Seen", first, last, "chest"]
        words, x = [], 0.1
        for n, t in enumerate(texts, start=1):
            words.append(OcrWord(f"p1w{n}", t, box(x, t), 0.8, handwritten=True))
            x += 0.12 * len(t) + 0.15
        return OcrResult(model="layout", pages=(OcrPage(1, 4.0, 2.0, tuple(words)),))


async def test_a_typed_out_page_is_read_again_for_boxes_before_masking(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(second_reading.services, "ocr", FreshOcr())
    await record_identity(aconn, crypto, world["case"])
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"], source="expert", typed=True)
    client = await client_for(world["expert"])
    job = (await client.post(url(world))).json()["job_id"]
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json()["status"] == "done"
    [sent] = model.requests
    assert sent.image is not None and sent.image.masked_words == 2


async def test_a_typed_out_page_with_no_way_to_find_the_boxes_is_refused(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"], source="expert", typed=True)
    client = await client_for(world["expert"])
    job = (await client.post(url(world))).json()["job_id"]
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json()["reason"] == "no_place"
    assert model.requests == []


async def test_a_busy_model_leaves_the_request_waiting(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core.errors import ProcessingPaused

    busy = ScriptedBedrock([ServiceBusy()])
    monkeypatch.setattr(second_reading.services, "kit", AiKit(cell(), busy, sleep=_no_sleep, retries=1))
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    client = await client_for(world["expert"])
    job = (await client.post(url(world, "?on_request=true"))).json()["job_id"]
    with pytest.raises(ProcessingPaused):
        await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json()["status"] == "queued"


async def _no_sleep(_: float) -> None:
    return None


# --- review of PR 172 ---


async def _asked(client: Any, w: dict[str, Any]) -> str:
    return str((await client.post(url(w, "?on_request=true"))).json()["job_id"])


async def test_a_page_turned_since_it_was_asked_for_is_refused_even_when_the_frame_would_pass(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    page = await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    client = await client_for(world["expert"])
    job = await _asked(client, world)
    admin.execute("update app.pages set rotation = 180 where id = %s", (page,))  # a half turn keeps the shape
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json() == {"status": "refused", "reason": "page_changed", "lines": []}
    assert model.requests == []


async def test_a_page_job_still_pending_refuses_because_the_picture_may_be_redrawn(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    client = await client_for(world["expert"])
    job = await _asked(client, world)
    admin.execute(
        "insert into app.jobs (case_id, kind, status) values (%s, 'process_pages', 'queued')", (world["case"],)
    )
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json()["reason"] == "page_changed"
    assert model.requests == []


class MissesTheName:
    """OCR that did not find the patient's name on the picture."""

    async def analyse(self, document: bytes, model: str) -> OcrResult:
        words = tuple(OcrWord(f"p1w{n}", t, box(0.1 + n * 0.6, t), 0.8) for n, t in enumerate(["Seen", "today"], 1))
        return OcrResult(model="layout", pages=(OcrPage(1, 4.0, 2.0, words),))


async def test_a_name_in_the_experts_text_that_the_fresh_reading_missed_is_refused(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(second_reading.services, "ocr", MissesTheName())
    await record_identity(aconn, crypto, world["case"])
    first, last = NAME.split()
    await make_page(aconn, storage, crypto, world["case"], ["Seen", first, last], source="expert", typed=True)
    client = await client_for(world["expert"])
    job = (await client.post(url(world))).json()["job_id"]
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json() == {"status": "refused", "reason": "identifier_unmatched", "lines": []}
    assert model.requests == []


async def test_an_unexpected_error_marks_the_request_failed_with_a_code(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(second_reading.services, "kit", AiKit(cell(), ScriptedBedrock([RuntimeError("boom")])))
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    client = await client_for(world["expert"])
    job = await _asked(client, world)
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json() == {"status": "failed", "reason": "unexpected_error", "lines": []}


async def test_a_job_that_began_before_a_new_request_cannot_overwrite_it(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class AskedAgain(ScriptedBedrock):
        async def invoke(self, request: Any) -> Any:
            await aconn.execute("update app.second_readings set requested_at = requested_at + interval '1 minute'")
            return await super().invoke(request)

    monkeypatch.setattr(second_reading.services, "kit", AiKit(cell(), AskedAgain([response(LINES, 800, 20)])))
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    client = await client_for(world["expert"])
    job = await _asked(client, world)
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json() == {"status": "queued", "reason": None, "lines": []}


# --- second review of PR 172 ---


class FindsTheNameOnce:
    """Fresh OCR that finds the surname once, where the expert's own text has it twice (header and footer)."""

    async def analyse(self, document: bytes, model: str) -> OcrResult:
        _, last = NAME.split()
        words = tuple(OcrWord(f"p1w{n}", t, box(0.1 + n * 1.0, t), 0.8) for n, t in enumerate([last, "Seen"], 1))
        return OcrResult(model="layout", pages=(OcrPage(1, 4.0, 2.0, words),))


async def test_a_surname_the_expert_typed_twice_but_the_fresh_reading_found_once_is_refused(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(second_reading.services, "ocr", FindsTheNameOnce())
    await record_identity(aconn, crypto, world["case"])
    _, last = NAME.split()
    await make_page(aconn, storage, crypto, world["case"], [last, "Seen", last], source="expert", typed=True)
    client = await client_for(world["expert"])
    job = (await client.post(url(world))).json()["job_id"]
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json()["reason"] == "identifier_unmatched"
    assert model.requests == []


async def test_a_failed_page_job_after_a_turn_refuses_because_the_picture_may_not_have_been_redrawn(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    page = await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    admin.execute("update app.pages set rotation = 180 where id = %s", (page,))
    failed = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'process_pages') returning id", (world["case"],)
    ).fetchone()
    assert failed is not None
    admin.execute("update app.jobs set status = 'running' where id = %s", (failed[0],))
    admin.execute("update app.jobs set status = 'failed' where id = %s", (failed[0],))
    client = await client_for(world["expert"])
    job = await _asked(client, world)
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json()["reason"] == "page_changed"
    assert model.requests == []


async def test_a_picture_replaced_since_it_was_asked_for_refuses(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    page = await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    client = await client_for(world["expert"])
    job = await _asked(client, world)
    path = f"cases/{world['case']}/pages/{page}/image.jpg"
    again = io.BytesIO()
    Image.new("RGB", (4 * DPI, 2 * DPI), "grey").save(again, format="JPEG")
    await storage.put(path, await crypto.encrypt(aconn, world["case"], again.getvalue(), purpose=path))
    await run(worker_db, admin, world, job)
    assert (await client.get(url(world))).json()["reason"] == "page_changed"
    assert model.requests == []
