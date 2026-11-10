"""File lifecycle audit, step 12: virus scanning (leads V1 and V2).

Each test states the SAFE behaviour. Where the application is not safe, the safe assertion is kept and the
test is marked `xfail(strict=True)` with the finding id, so a fix turns it into a failure to remove the mark.
Every "blocked" assertion has a positive control: the same route or job lets the clean file through, or the
same file is caught by another path. The EICAR test string is assembled in `file_lifecycle_documents`.
"""

from __future__ import annotations

import asyncio
import contextlib
import email.message
import io
import json
import re
import struct
import uuid
import zipfile
from collections.abc import AsyncIterable, AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.config import ConfigError
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.scan import ClamdScanner, RecordedScanner, ScanResult, ScanUnavailable, scanner_from_env
from app.core.storage import CaseFileStorage, case_prefix
from app.features.close import jobs as close_jobs
from app.features.pipeline import service as pipeline_service
from app.features.pipeline import wiring
from app.features.setup.jobs import unread_letters
from tests.repository.test_pipeline_support import Harness, empty_answer, harness
from tests.support.ai import ScriptedBedrock
from tests.support.db import ClientFor
from tests.support.file_lifecycle_documents import (
    eicar,
    pdf_with_eicar,
    sha,
    text_pdf,
    upload_bytes,
    zip_of,
)
from tests.support.file_lifecycle_uploads import upload_whole
from tests.support.files import CHUNK, build_app, local_storage
from tests.support.packs import FIXTURE
from tests.support.seed import Seed
from tests.support.uploads import complete_and_finalize

pytestmark = pytest.mark.anyio

APP_DIR = Path(__file__).resolve().parents[2] / "app"
SAFE_REFUSALS = (403, 404, 409, 410, 423)
TOLD_WORDS = ("infect", "blocked for safety", "virus", "malware", "unsafe")


# --- fixtures --------------------------------------------------------------------------------------


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


def scan_row(h: Harness, document: str) -> tuple[bool, str | None] | None:
    found = h.admin.execute("select clean, signature from app.document_scans where document_id = %s", (document,))
    row = found.fetchone()
    return None if row is None else (row[0], row[1])


def audit_actions(h: Harness, document: str) -> list[str]:
    rows = h.admin.execute(
        "select action from app.audit_log where case_id = %s and target = %s", (h.case_id, f"document:{document}")
    ).fetchall()
    return [r[0] for r in rows]


def page_documents(h: Harness) -> set[str]:
    return {str(p["document_id"]) for p in h.pages()}


def scan_job(h: Harness) -> uuid.UUID:
    row = h.admin.execute(
        "select id from app.jobs where case_id = %s and kind = 'scan_document' order by created_at desc limit 1",
        (h.case_id,),
    ).fetchone()
    assert row is not None, "the upload queued no scan job"
    return row[0]  # type: ignore[no-any-return]


def _children(h: Harness, exclude: set[str]) -> list[str]:
    """Documents of the case's bundle other than `exclude`: what unpacking made."""
    rows = h.admin.execute(
        "select id from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)"
        " order by created_at, id",
        (h.case_id,),
    ).fetchall()
    return [str(r[0]) for r in rows if str(r[0]) not in exclude]


class _Recording:
    """Wraps a scanner: remembers the SHA-256 of every stream it is asked to scan, and can be told to flag some."""

    def __init__(self, flag: set[str] | None = None) -> None:
        self.seen: list[str] = []
        self.flag = flag or set()

    async def scan(self, chunks: AsyncIterable[bytes]) -> ScanResult:
        data = b"".join([c async for c in chunks])
        self.seen.append(sha(data))
        if sha(data) in self.flag:
            return ScanResult(clean=False, signature="Synthetic-Test-Signature")
        return ScanResult(clean=True)


# --- V1: which scanner a cell runs -----------------------------------------------------------------


def test_v1_the_scanner_settings_can_choose_clamd_and_refuse_nonsense() -> None:
    """Positive controls for the V1 tests below: the switch exists and does what the ADR says."""
    assert isinstance(scanner_from_env({"VIRUS_SCANNER": "clamd", "CLAMD_HOST": "clamd.internal"}), ClamdScanner)
    assert isinstance(scanner_from_env({"CLAMD_HOST": "clamd.internal"}), ClamdScanner)
    assert isinstance(scanner_from_env({"VIRUS_SCANNER": "recorded"}), RecordedScanner)
    for env in ({"VIRUS_SCANNER": "off"}, {"VIRUS_SCANNER": "clamd"}, {"VIRUS_SCANNER": "none", "CLAMD_HOST": "x"}):
        with pytest.raises(ConfigError):
            scanner_from_env(env)


@pytest.mark.parametrize(
    "env",
    [
        pytest.param(
            {"REGION": "uk"},
            marks=pytest.mark.xfail(
                strict=True,
                reason="AUDIT-12 V1 medium: a cell with VIRUS_SCANNER and CLAMD_HOST both unset silently runs the "
                "EICAR-only RecordedScanner, so every file is 'clean': app/core/scan.py:109-116, "
                "app/features/pipeline/wiring.py:99",
            ),
            id="unset",
        ),
        pytest.param(
            {"REGION": "uk", "VIRUS_SCANNER": "recorded"},
            marks=pytest.mark.xfail(
                strict=True,
                reason="AUDIT-12 POLICY V1b: ADR 0027 keeps `recorded` selectable in any cell; nothing ties it "
                "to tests and local: app/core/scan.py:115-116",
            ),
            id="recorded-in-a-cell",
        ),
    ],
)
def test_v1_a_live_cell_never_runs_the_stand_in_scanner(env: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        scanner_from_env(env)


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 POLICY V1c: nothing calls ClamdScanner.ping; docs/decisions ADR 0027 says so ('the worker has "
    "no ready hook yet') and scanning fails closed (uploads wait unscanned): app/core/scan.py:84, "
    "docs/decisions/0027-clamav-private-railway-service.md",
)
def test_v1_the_app_checks_that_the_scanner_answers_before_it_is_ready() -> None:
    """A source search, not a run: there is no readiness route that touches the scanner to drive (the one cell route,
    `GET /v1/cell`, reports where files are kept and builds no scanner), so the check is that some module calls
    `ClamdScanner.ping` at all."""
    defined = "async def ping" in (APP_DIR / "core" / "scan.py").read_text()
    assert defined, "control: the readiness check exists in the scanner"
    users = [
        str(p.relative_to(APP_DIR))
        for p in APP_DIR.rglob("*.py")
        if p.name != "scan.py"
        and re.search(r"scanner\w*\.ping\(|\.ping\(\)", p.read_text())
        and "scan" in p.read_text()
    ]
    assert users, "no module checks the scanner's readiness"


# --- V2a: the stand-in sees a signature however the stream is cut ----------------------------------


async def _stream(*pieces: bytes) -> AsyncIterator[bytes]:
    for piece in pieces:
        yield piece


async def test_v2a_the_signature_is_found_wherever_the_stream_is_cut() -> None:
    needle = eicar()
    scanner = RecordedScanner()
    pad = b"synthetic padding " * 3
    for cut in range(1, len(needle)):
        verdict = await scanner.scan(_stream(pad + needle[:cut], needle[cut:] + pad))
        assert verdict.clean is False, f"missed when cut at {cut}"
    three = await scanner.scan(_stream(needle[:5], needle[5:30], needle[30:]))
    assert three.clean is False
    # positive control for "clean": the same bytes without the signature, cut the same way
    assert (await scanner.scan(_stream(pad + b"x", b"y" + pad))).clean is True
    assert (await scanner.scan(_stream(needle[:-1], b"?" + pad))).clean is True, "one byte off is not the signature"


# --- V2a/V2f: fake clamd, the wire and the verdicts ------------------------------------------------


@contextlib.asynccontextmanager
async def fake_clamd(reply: bytes) -> AsyncIterator[tuple[int, bytearray]]:
    seen = bytearray()

    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while not seen.endswith(b"\x00\x00\x00\x00"):
                got = await reader.read(65536)
                if not got:
                    break
                seen.extend(got)
            writer.write(reply)
            await writer.drain()
        finally:
            writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    try:
        yield server.sockets[0].getsockname()[1], seen
    finally:
        server.close()
        await server.wait_closed()


async def test_v2a_clamd_gets_the_whole_stream_in_zinstream_framing_and_a_clean_reply_is_clean() -> None:
    body = [b"A" * 1000, b"B" * 10]
    async with fake_clamd(b"stream: OK\0") as (port, seen):
        verdict = await ClamdScanner("127.0.0.1", port, timeout_s=5).scan(_stream(*body))
    assert verdict == ScanResult(clean=True)
    expected = b"zINSTREAM\0" + b"".join(struct.pack(">I", len(b)) + b for b in body) + struct.pack(">I", 0)
    assert bytes(seen) == expected


@pytest.mark.parametrize(
    "reply",
    [
        b"stream: OK\0stream: Win.Test.EICAR_HDB-1 FOUND\0",
        b"stream: OK\nstream: Win.Test.EICAR_HDB-1 FOUND\n",
        b"stream: Win.Test.EICAR_HDB-1 FOUND\0",
        b"INSTREAM size limit exceeded. ERROR\0",
        b"stream: lstat() failed. ERROR\0",
        b"stream: OK FOUND\0",
        b"STREAM: OK\0",
        b"ok\0",
        b"",
        b"\0",
        b"<html>gateway</html>",
    ],
)
async def test_v2f_only_a_plain_ok_is_clean_everything_else_is_a_finding_or_no_verdict(reply: bytes) -> None:
    async with fake_clamd(reply) as (port, _):
        try:
            verdict = await ClamdScanner("127.0.0.1", port, timeout_s=5).scan(_stream(b"synthetic"))
        except ScanUnavailable:
            return  # no verdict: callers fail closed
    assert verdict.clean is False


async def test_v2f_a_server_that_closes_before_answering_gives_no_verdict() -> None:
    async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await reader.read(10)
        writer.close()

    server = await asyncio.start_server(handle, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        with pytest.raises(ScanUnavailable):
            await ClamdScanner("127.0.0.1", port, timeout_s=5).scan(_stream(b"x" * 2_000_000))
    finally:
        server.close()
        await server.wait_closed()


# --- V2a: an infected upload through the real jobs -------------------------------------------------


async def test_v2a_an_infected_file_is_flagged_audited_and_never_becomes_pages(h: Harness) -> None:
    _, bad = await upload_bytes(h, pdf_with_eicar(1), filename="bad.pdf")
    _, good = await upload_bytes(h, text_pdf(2), filename="good.pdf")
    finished = await h.drain()
    assert set(finished.values()) == {"done"}

    flagged = scan_row(h, bad)
    assert flagged is not None and flagged[0] is False and flagged[1], "the scan is recorded as infected"
    assert audit_actions(h, bad).count("document.infected") == 1
    assert scan_row(h, good) == (True, None), "control: the clean file is recorded clean"
    assert "document.infected" not in audit_actions(h, good)
    assert page_documents(h) == {good}, "only the clean file became pages (control: pages exist)"
    assert [p["bundle_page_no"] for p in h.pages()] == [1, 2]
    split = h.admin.execute("select split_at from app.document_scans where document_id = %s", (bad,)).fetchone()
    assert split == (None,), "the infected file was never split"


async def test_v2a_a_signature_across_the_8_mib_part_boundary_is_found_through_the_upload(h: Harness) -> None:
    lead = b"%PDF-1.4\n"
    tail = 20  # the signature starts this many bytes before the end of part 1 and ends in part 2
    body = lead + b"A" * (CHUNK - len(lead) - tail)
    infected = body + eicar() + b"\n"
    control = body + b"B" * len(eicar()) + b"\n"
    assert infected.index(eicar()) < CHUNK < infected.index(eicar()) + len(eicar())
    assert len(infected) == len(control)

    _, bad = await upload_bytes(h, infected, filename="boundary.pdf")
    _, ok = await upload_bytes(h, control, filename="control.pdf")
    await h.drain()
    verdict = scan_row(h, bad)
    assert verdict is not None and verdict[0] is False, "the infected file was flagged across the boundary"
    assert scan_row(h, ok) == (True, None), "control: the same size without the signature is clean"
    assert str(bad) not in page_documents(h)


# --- V2f: no verdict means nothing is accepted -----------------------------------------------------


class _Failing:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def scan(self, chunks: AsyncIterable[bytes]) -> ScanResult:
        async for _ in chunks:
            break
        raise self.error


@pytest.mark.parametrize(
    "error", [ScanUnavailable("down"), OSError("reset"), RuntimeError("bug"), TimeoutError()], ids=str
)
async def test_v2f_a_scanner_that_fails_in_any_way_accepts_nothing_and_recovers(h: Harness, error: Exception) -> None:
    _, doc = await upload_bytes(h, text_pdf(1), filename="a.pdf")
    job = scan_job(h)
    services = wiring.current()
    working = services.scanner
    services.scanner = _Failing(error)
    try:
        with pytest.raises(type(error)):
            await pipeline_service.scan_document(JobContext(h.worker_db, job, h.case_id, "scan_document", 1))
    finally:
        services.scanner = working
    assert scan_row(h, doc) is None, "no verdict was recorded"
    assert h.pages() == []
    assert "process_bundle" not in [
        r[0] for r in h.admin.execute("select kind from app.jobs where case_id = %s", (h.case_id,))
    ]
    await pipeline_service.scan_document(JobContext(h.worker_db, job, h.case_id, "scan_document", 2))
    assert scan_row(h, doc) == (True, None), "control: with a working scanner the same job accepts it"


# --- V2b: a flagged or unscanned file is not handed back -------------------------------------------


async def _fetch(h: Harness, doc: str) -> Any:
    return await h.client.get(f"/v1/cases/{h.case_id}/documents/{doc}/file")


async def test_v2b_control_a_clean_file_and_an_unknown_one(h: Harness) -> None:
    data = text_pdf(1)
    _, good = await upload_bytes(h, data, filename="good.pdf")
    await h.drain()
    ok = await _fetch(h, good)
    assert ok.status_code == 200 and sha(ok.content) == sha(data)
    assert ok.headers["cache-control"] == "private, no-store"
    assert (await _fetch(h, str(uuid.uuid4()))).status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V2b medium: GET /v1/cases/{case}/documents/{doc}/file returns 200 with the decrypted bytes of a "
    "document whose scan is recorded infected (clean=false), whether it was uploaded or unpacked from a zip: "
    "app/core/routes/uploads.py:443-497 (no scan_row check)",
)
@pytest.mark.parametrize("origin", ["upload", "zip-member"])
async def test_v2b_a_file_the_scan_flagged_is_not_served_back(h: Harness, origin: str) -> None:
    """`zip-member`: a member is never scanned (V2g), so its verdict can only be set by hand (as admin); the route
    must still refuse a member recorded infected."""
    _, good = await upload_bytes(h, text_pdf(1), filename="good.pdf")
    if origin == "upload":
        _, bad = await upload_bytes(h, pdf_with_eicar(1), filename="bad.pdf")
        await h.drain()
        served = pdf_with_eicar(1)
    else:
        served = text_pdf(2, "member")
        _, outer = await upload_bytes(h, zip_of({"inner.pdf": served}), filename="wrapped.zip")
        await h.drain()
        members = _children(h, exclude={good, outer})
        assert len(members) == 1, "premise: the member became a document of its own"
        bad = members[0]
        member_ok = await _fetch(h, bad)
        assert member_ok.status_code == 200 and sha(member_ok.content) == sha(served), "control: it is served clean"
        h.admin.execute(
            "update app.document_scans set clean = false, signature = 'Synthetic-Test-Signature'"
            " where document_id = %s",
            (bad,),
        )
    assert (await _fetch(h, good)).status_code == 200, "control: the clean file is served"
    flagged = scan_row(h, bad)
    assert flagged is not None and flagged[0] is False, "control: the scan is recorded as infected"
    assert (await _fetch(h, bad)).status_code in SAFE_REFUSALS


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V2b2 medium: the same route serves a document whose scan job has not run (no document_scans row): "
    "app/core/routes/uploads.py:443-497",
)
async def test_v2b_a_file_not_yet_scanned_is_not_served_back(h: Harness) -> None:
    _, waiting = await upload_bytes(h, pdf_with_eicar(1), filename="waiting.pdf")
    assert scan_row(h, waiting) is None, "control: no scan has run yet"
    assert (await _fetch(h, waiting)).status_code in SAFE_REFUSALS


# --- V2c: the expert is told ------------------------------------------------------------------------


async def _what_the_expert_can_see(h: Harness, upload: str) -> str:
    seen = []
    for url in (
        f"/v1/uploads/{upload}",
        f"/v1/cases/{h.case_id}/bundle/summary",
        f"/v1/cases/{h.case_id}/files",
        "/v1/notifications",
    ):
        resp = await h.client.get(url)
        if resp.status_code == 200:
            seen.append(json.dumps(resp.json()).lower())
    assert seen, "no listing answered"
    return " ".join(seen)


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V2c low: after an infected upload the upload status, bundle summary, files list and notifications "
    "say nothing about it; only the audit row `document.infected` exists, so the expert sees a document with no "
    "pages and no reason (failure mode F3 promises 'This file was blocked for safety'): "
    "app/features/pipeline/service.py:146, docs/spec/failure-modes.md F3",
)
async def test_v2c_the_expert_is_told_the_file_was_blocked(h: Harness) -> None:
    clean_upload, _ = await upload_bytes(h, text_pdf(1), filename="good.pdf")
    bad_upload, bad = await upload_bytes(h, pdf_with_eicar(1), filename="bad.pdf")
    await h.drain()
    before = await _what_the_expert_can_see(h, clean_upload)
    assert not any(w in before for w in TOLD_WORDS), "control: a clean upload is not described as blocked"
    assert "document.infected" in audit_actions(h, bad), "control: the finding exists in the audit log"
    after = await _what_the_expert_can_see(h, bad_upload)
    assert any(w in after for w in TOLD_WORDS)


# --- V2d: the export ---------------------------------------------------------------------------------


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage, crypto: CaseCrypto) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=crypto)


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V2d latent low: the export does not filter on the scan verdict, unreachable while D6c breaks the "
    "export of real uploads: app/features/close/jobs.py:343-348,392-398",
)
async def test_v2d_the_full_export_carries_a_clean_real_upload_and_not_a_flagged_or_unscanned_one(
    seed: Seed,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    client_for: ClientFor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On real uploads (their `storage_path` is the upload prefix, as in production). Today the full export fails
    outright on any real upload (D6c, close/jobs.py:392-398), so the scan verdict is not even reached."""
    monkeypatch.setattr(close_jobs.services, "storage", storage)
    monkeypatch.setattr(close_jobs.services, "crypto", crypto)
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    case = seed.case(org, expert, status="served")
    admin.execute("update app.cases set status = 'settled' where id = %s", (case,))
    client = await client_for(expert)
    sent = {
        "good": text_pdf(1, "export good"),
        "bad": text_pdf(1, "export flagged") + b"\n%" + eicar() + b"\n",
        "waiting": text_pdf(1, "export waiting"),
    }
    document: dict[str, str] = {}
    for label, data in sent.items():
        upload = await upload_whole(client, case, data, filename=f"{label}.pdf")
        document[label] = await complete_and_finalize(client, upload, sha(data))
    for label, verdict in (("good", (True, None)), ("bad", (False, "Eicar-Test-Signature"))):  # `waiting`: no row
        admin.execute(
            "insert into app.document_scans (document_id, bundle_id, clean, signature)"
            " values (%s, (select bundle_id from app.cases where id = %s), %s, %s)",
            (document[label], case, *verdict),
        )
    job = uuid.UUID((await client.post(f"/v1/cases/{case}/export")).json()["job_id"])
    await close_jobs.export_case(JobContext(worker_db, job, case, "export_case", 1))
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    resp = await client.get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 200, resp.text
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    carried = [archive.read(name) for name in archive.namelist()]
    assert sent["good"] in carried, "control: the clean real upload is in the export"
    assert sent["bad"] not in carried, "the export carries a document the scan flagged"
    assert sent["waiting"] not in carried, "the export carries a document that was never scanned"


# --- V2e: letters are read by OCR and Claude without a scan ---------------------------------------


async def _letters(
    h: Harness, seed: Seed, scans: dict[str, tuple[bool, str | None] | None]
) -> tuple[dict[str, int], set[int]]:
    numbers: dict[str, int] = {}
    for n, (label, scan) in enumerate(scans.items(), start=1):
        doc = seed.document(h.case_id)
        h.admin.execute(
            "update app.documents set storage_path = %s where id = %s",
            (f"{case_prefix(h.case_id)}/uploads/{doc}", doc),
        )
        if scan is not None:
            h.admin.execute(
                "insert into app.document_scans (document_id, bundle_id, clean, signature)"
                " values (%s, (select bundle_id from app.cases where id = %s), %s, %s)",
                (doc, h.case_id, *scan),
            )
        h.admin.execute(
            "insert into app.instruction_versions (case_id, version_no, kind, document_id) values (%s, %s, 'letter', "
            "%s)",
            (h.case_id, n, doc),
        )
        numbers[label] = n
    async with h.worker_db.job(h.case_id) as conn:
        listed = {no for _, no, _ in await unread_letters(conn, h.case_id)}
    return numbers, listed


async def test_v2e_control_a_clean_letter_is_listed_for_reading(h: Harness, seed: Seed) -> None:
    numbers, listed = await _letters(h, seed, {"good": (True, None)})
    assert listed == {numbers["good"]}


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V2e low: `unread_letters` returns a letter whose document is recorded infected, so "
    "extract_questions OCRs it (Azure) and sends it to Claude: app/features/setup/jobs.py:72-95 (no "
    "document_scans join)",
)
async def test_v2e_an_infected_letter_is_not_read(h: Harness, seed: Seed) -> None:
    numbers, listed = await _letters(h, seed, {"good": (True, None), "bad": (False, "Eicar-Test-Signature")})
    assert numbers["good"] in listed, "control: the clean letter is listed"
    assert numbers["bad"] not in listed


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V2e2 low: `unread_letters` also returns a letter whose document has no scan row yet: "
    "app/features/setup/jobs.py:72-95",
)
async def test_v2e_a_letter_not_yet_scanned_is_not_read(h: Harness, seed: Seed) -> None:
    numbers, listed = await _letters(h, seed, {"good": (True, None), "waiting": None})
    assert numbers["good"] in listed, "control: the clean letter is listed"
    assert numbers["waiting"] not in listed


# --- V2g: files inside an archive or an email are not scanned on their own ------------------------


def _eml(attachment: bytes) -> bytes:
    msg = email.message.EmailMessage()
    msg["From"] = "a@example.invalid"
    msg["To"] = "b@example.invalid"
    msg["Subject"] = "synthetic"
    msg.set_content("synthetic body")
    msg.add_attachment(attachment, maintype="application", subtype="pdf", filename="inner.pdf")
    return msg.as_bytes()


def _wrap(carrier: str, leaf: bytes) -> bytes:
    return zip_of({"inner.pdf": leaf}) if carrier == "zip" else _eml(leaf)


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V2g low: a file unpacked from a zip or email is recorded clean without its own scan; safety "
    "rests on the scanner having looked inside the parent: app/features/pipeline/repo.py:285-289",
)
@pytest.mark.parametrize("carrier", ["zip", "eml"])
async def test_v2g_a_file_inside_an_archive_or_email_is_scanned_on_its_own(h: Harness, carrier: str) -> None:
    """Not about what a scanner can see: the stand-in cannot look inside a deflated zip or a base64 attachment, but
    in production the whole parent goes through clamd first (`scan_document`), and clamd inspects archives and
    emails. What is true either way: the member is never passed to the scanner and gets a `clean = true` row."""
    leaf = text_pdf(2, "member")
    wrapped = _wrap(carrier, leaf)
    scanner = _Recording()
    services = wiring.current()
    original = services.scanner
    services.scanner = scanner
    try:
        _, outer = await upload_bytes(h, wrapped, filename=f"wrapped.{carrier}")
        await h.drain()
    finally:
        services.scanner = original
    members = _children(h, exclude={outer})
    assert len(members) == 1, "premise: the member became a document of its own"
    assert scanner.seen == [sha(wrapped)], "premise: the scanner was given the parent, once"
    assert scan_row(h, members[0]) == (True, None), "premise: the member is recorded clean"
    assert sha(leaf) in scanner.seen, "the member's own bytes were never given to the scanner"


@pytest.mark.parametrize("carrier", ["zip", "eml"])
async def test_v2g_nothing_is_unpacked_from_a_parent_the_scanner_flags(h: Harness, carrier: str) -> None:
    """The other half of the premise above: the parent's verdict gates the unpacking."""
    bad_leaf, ok_leaf = text_pdf(1, "flagged parent"), text_pdf(1, "clean parent")
    bad_parent, ok_parent = _wrap(carrier, bad_leaf), _wrap(carrier, ok_leaf)
    scanner = _Recording(flag={sha(bad_parent)})
    services = wiring.current()
    original = services.scanner
    services.scanner = scanner
    try:
        _, flagged = await upload_bytes(h, bad_parent, filename=f"flagged.{carrier}")
        _, fine = await upload_bytes(h, ok_parent, filename=f"fine.{carrier}")
        await h.drain()
    finally:
        services.scanner = original
    assert scan_row(h, flagged) == (False, "Synthetic-Test-Signature")
    assert scan_row(h, fine) == (True, None)
    made = _children(h, exclude={flagged, fine})
    assert len(made) == 1, "control: the clean parent was unpacked into one member"
    kept = {str(r[0]) for r in h.admin.execute("select parent_document_id from app.document_parts")}
    assert flagged not in kept and fine in kept, "no member was recorded for the flagged parent"
    parts = [k for k in await h.storage.list_recursive(case_prefix(h.case_id)) if "/parts/" in k]
    assert len(parts) == 1 and made[0] in parts[0], "only the clean parent's member was stored"
    assert flagged not in page_documents(h), "the flagged parent made no page"
    assert page_documents(h) <= {fine, made[0]}, "pages come only from the clean parent and its member"
