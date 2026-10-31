"""Slice A increment 11c-2: files that are not PDFs (F36, architecture 10.3 "Inputs other than PDF").

- An email becomes a page of its own; each attachment becomes a document of its own (`part_of` the
  email) and is read as usual. A zip has no pages itself; each file inside becomes a document.
- Word files give typed pages, their words read from the file (no OCR).
- Audio, video and imaging are exhibits: stored, listed in `GET /v1/cases/{id}/exhibits` and in the
  bundle summary, never given page numbers and never sent to the model.
- Documents list their media type and what they were part of. Exhibits and parts are kept per case;
  only the worker records them.
Runs under the launch and the fixture packs. The contents are made up.
"""

from __future__ import annotations

import io
import uuid
import wave
import zipfile
from collections.abc import AsyncIterator
from email.message import EmailMessage
from typing import Any

import psycopg
import pytest
from docx import Document

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_quarantine import answer, summary
from tests.repository.test_pipeline_support import Harness, harness, page_words
from tests.support.ai import ScriptedBedrock
from tests.support.db import ClientFor, scope_for
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def docx(*paragraphs: str) -> bytes:
    document = Document()
    for text in paragraphs:
        document.add_paragraph(text)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def wav() -> bytes:
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\0\0" * 800)
    return out.getvalue()


def eml(attachment: bytes | None = None) -> bytes:
    message = EmailMessage()
    message["From"] = "records@example.test"
    message["To"] = "expert@example.test"
    message["Subject"] = "Synthetic further records"
    message["Date"] = "Mon, 05 Oct 2026 09:00:00 +0000"
    message.set_content("Synthetic covering note\nPlease find the letter attached")
    if attachment is not None:
        message.add_attachment(
            attachment,
            maintype="application",
            subtype="vnd.openxmlformats-officedocument.wordprocessingml.document",
            filename="referral-letter.docx",
        )
    return message.as_bytes()


def zipped(files: dict[str, bytes]) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return out.getvalue()


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
    pack = default_library().combine(*combo.codes)
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([answer(pack)]),
    ) as running:
        yield running


async def documents(h: Harness) -> dict[str, dict[str, Any]]:
    return {d["filename"]: d for d in (await summary(h))["documents"]}


async def exhibits(h: Harness) -> list[dict[str, Any]]:
    got = await h.client.get(f"/v1/cases/{h.case_id}/exhibits")
    assert got.status_code == 200, got.status_code
    return got.json()["exhibits"]  # type: ignore[no-any-return]


async def texts(h: Harness, n: int) -> list[str]:
    page = next(p for p in h.pages() if p["bundle_page_no"] == n)
    return [text for text, _ in (await page_words(h, page["id"])).values()]


async def test_an_email_is_a_page_and_its_attachment_a_document_of_its_own(h: Harness) -> None:
    email_id = await h.upload(eml(docx("Synthetic referral letter")), filename="further-records.eml")
    await h.drain()

    assert [p["bundle_page_no"] for p in h.pages()] == [1, 2]
    got = await documents(h)
    email, letter = got["further-records.eml"], got["referral-letter.docx"]
    assert (email["id"], email["media_type"], email["part_of"]) == (str(email_id), "message/rfc822", None)
    assert (email["first_page_no"], email["last_page_no"]) == (1, 1)
    assert (letter["media_type"], letter["part_of"]) == (DOCX, str(email_id))
    assert (letter["first_page_no"], letter["last_page_no"]) == (2, 2)
    assert letter["bundle_version"] == email["bundle_version"]
    assert {"Synthetic", "further", "records"} <= set(await texts(h, 1))
    assert await texts(h, 2) == ["Synthetic", "referral", "letter"], "the Word file's own text was not used"
    assert await exhibits(h) == []


async def test_a_recording_is_an_exhibit_with_no_page_number_and_no_ai_call(h: Harness) -> None:
    recording = wav()
    document_id = await h.upload(recording, filename="voicemail.wav")
    await h.drain()

    assert h.pages() == [], "a recording was given page numbers"
    assert h.bedrock.requests == [], "a recording was sent to the model"
    (exhibit,) = await exhibits(h)
    assert {k: exhibit[k] for k in ("document_id", "filename", "media_type", "size", "part_of")} == {
        "document_id": str(document_id),
        "filename": "voicemail.wav",
        "media_type": "audio/wav",
        "size": len(recording),
        "part_of": None,
    }
    assert [e["filename"] for e in (await summary(h))["exhibits"]] == ["voicemail.wav"]
    assert (await documents(h))["voicemail.wav"]["pages"] == 0


async def test_a_zip_is_unpacked_with_a_manifest(h: Harness) -> None:
    files = {"letters/referral-letter.docx": docx("Synthetic letter"), "voicemail.wav": wav()}
    zip_id = await h.upload(zipped(files), filename="records-batch.zip")
    await h.drain()

    got = await documents(h)
    assert set(got) == {"records-batch.zip", "letters/referral-letter.docx", "voicemail.wav"}
    assert (got["records-batch.zip"]["media_type"], got["records-batch.zip"]["pages"]) == ("application/zip", 0)
    assert all(got[name]["part_of"] == str(zip_id) for name in files)
    assert (got["letters/referral-letter.docx"]["first_page_no"], len(h.pages())) == (1, 1)
    assert await texts(h, 1) == ["Synthetic", "letter"]
    (exhibit,) = await exhibits(h)
    assert (exhibit["filename"], exhibit["part_of"]) == ("voicemail.wav", str(zip_id))

    await h.enqueue("process_bundle")
    await h.drain()
    assert len(h.pages()) == 1 and len(await documents(h)) == 3, "a zip was unpacked twice"
    assert len(await exhibits(h)) == 1


async def test_exhibits_are_kept_per_case_and_recorded_only_by_the_worker(
    h: Harness, seed: Seed, client_for: ClientFor
) -> None:
    document_id = await h.upload(wav(), filename="voicemail.wav")
    await h.drain()
    viewer = seed.person()
    seed.case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    assert (await (await client_for(viewer)).get(f"/v1/cases/{h.case_id}/exhibits")).status_code == 200
    outsider = await client_for(seed.person())
    assert (await outsider.get(f"/v1/cases/{h.case_id}/exhibits")).status_code == 404

    writes = [
        (
            "insert into app.exhibits (case_id, document_id, media_type, size, sha256, storage_path)"
            " values (%s, %s, 'audio/wav', 1, repeat('0', 64), 'x')",
            (h.case_id, document_id),
        ),
        (
            "insert into app.document_parts (case_id, parent_document_id, document_id, part_no, media_type)"
            " values (%s, %s, %s, 1, 'audio/wav')",
            (h.case_id, document_id, document_id),
        ),
        ("delete from app.exhibits where case_id = %s", (h.case_id,)),
    ]
    for sql, args in writes:
        async with h.api_db.request(scope_for(h.expert)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(sql, args)
    stranger = seed.person()
    async with h.api_db.request(scope_for(stranger)) as conn:
        cur = await conn.execute("select count(*) from app.exhibits where case_id = %s", (h.case_id,))
        assert await cur.fetchone() == (0,)
    unknown = await h.client.get(f"/v1/cases/{uuid.uuid4()}/exhibits")
    assert unknown.status_code == 404
