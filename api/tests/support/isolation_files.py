"""Real stored files for the cross-tenant download tests (`tests/security/..._tenant_isolation_downloads.py`).

The shared isolation app (`isolation_http.iso_app`) is built without a file store the tests can reach, and the
seeded rows hold placeholder ciphertext (`b"sealed"`), so even the owner is answered 404, 500 or 503 on a download.
Here the app gets a local S3 stand-in (moto, as the upload tests use) and a case-key provider the test also holds,
and each `seed_*` function puts a REAL sealed file in the store under the path the route reads, points the tenant's
row at it, and returns what the named expert must receive (`Obj`). Every file carries a token unique to its tenant
and its call, so a test can tell whose bytes a response holds. Everything is synthetic; nothing resembles a record.

`iso_app` here replaces the one in `isolation_http` (a test module imports this one, so `iso_send`, which asks for a
fixture called `iso_app`, builds its requests against this app).
"""

from __future__ import annotations

import base64
import hashlib
import io
import itertools
import json
import os
import uuid
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider, SecretBox
from app.core.storage import CaseFileStorage, case_prefix, upload_prefix
from tests.support.contracts import add_report_version
from tests.support.isolation_world import IsolationWorld, Tenant
from tests.support.seed import Seed

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
_COUNTER = itertools.count(7001)  # page numbers and version numbers no other seeder uses


@dataclass
class Env:
    admin: psycopg.Connection
    seed: Seed
    aconn: psycopg.AsyncConnection
    storage: CaseFileStorage
    crypto: CaseCrypto = field(default=CRYPTO)


@dataclass(frozen=True)
class Obj:
    """One stored object and how to ask for it: `suffix` is the path below `/v1/cases/{case_id}`."""

    suffix: str
    body: bytes  # exactly what the named expert receives (b"" when the answer is not byte-for-byte stable)
    tokens: tuple[bytes, ...]  # byte strings that identify this object in any response
    media: str
    params: dict[str, str] = field(default_factory=dict)
    case: uuid.UUID | None = None  # the case it lives on when that is not the tenant's main one
    has_id: bool = True  # whether its id (page number, document, version, job, package) is in the path

    def url(self, tenant: Tenant) -> str:
        return f"/v1/cases/{self.case or tenant.case}{self.suffix}"

    def in_(self, content: bytes) -> bool:
        """Whether a response body holds anything of this object."""
        return (bool(self.body) and self.body in content) or any(t in content for t in self.tokens)


Seeder = Callable[[Env, Tenant], Awaitable[Obj]]


def token_for(tenant: Tenant, what: str) -> str:
    """A string unique to this tenant, this kind of file and this call."""
    return f"{what}-{tenant.marker[:12]}-{uuid.uuid4().hex[:8]}"


# --- the app and the environment ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
async def iso_app(api_dsn: str, auth_env: dict[str, str]) -> AsyncIterator[FastAPI]:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.db import Database
    from app.core.email import OutboxMailer
    from app.main import create_app
    from tests.support.files import local_storage
    from tests.support.packs import FIXTURE

    database = Database(api_dsn, max_size=4)
    await database.open()
    storage = await local_storage(auth_env)
    try:
        yield create_app(
            database=database,
            region=str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"]),
            auth=SupabaseAuth(auth_settings(auth_env)),
            mailer=OutboxMailer(),
            storage=storage,
            crypto=CRYPTO,
        )
    finally:
        await storage.aclose()
        await database.close()


@pytest.fixture(scope="module")
async def dl_env(iso_app: FastAPI, admin: psycopg.Connection, admin_dsn: str, iso_seed: Seed) -> AsyncIterator[Env]:
    from app.features.report.services import ReportServices, configure

    configure(ReportServices(crypto=CRYPTO, storage=iso_app.state.storage))  # the spot check mails through it
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as aconn:
        yield Env(admin=admin, seed=iso_seed, aconn=aconn, storage=iso_app.state.storage)
    configure(None)


async def put_sealed(env: Env, case: uuid.UUID, path: str, data: bytes) -> None:
    await env.storage.put(path, await env.crypto.encrypt(env.aconn, case, data, purpose=path))


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def jpeg_of(token: str) -> bytes:
    return b"\xff\xd8\xff\xe0" + f"ISO-IMAGE {token}".encode() + b"\xff\xd9"


def pdf_of(token: str) -> bytes:
    """A real one-page PDF whose text is the token (reportlab writes the page uncompressed)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4, invariant=1, pageCompression=0)
    pdf.drawString(60, 760, token)
    pdf.showPage()
    pdf.save()
    return out.getvalue()


def zip_of(token: str) -> bytes:
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr("notes.txt", f"ISO-EXPORT {token}")
    return out.getvalue()


def _finish(admin: psycopg.Connection, job: uuid.UUID) -> None:
    for status in ("running", "done"):  # a job moves queued, running, done and no shorter way
        admin.execute("update app.jobs set status = %s where id = %s", (status, job))


def _one(admin: psycopg.Connection, sql: str, *args: Any) -> Any:
    row = admin.execute(sql, args).fetchone()
    assert row is not None, sql
    return row[0]


# --- one seeder per kind of download -----------------------------------------------------------------------------


async def seed_page(env: Env, t: Tenant) -> tuple[Obj, Obj]:
    """A new page with its stored picture and thumbnail: (page image, thumbnail)."""
    from app.features.pipeline import paths

    number = next(_COUNTER)
    page = env.seed.page(t.case, uuid.UUID(t.ids["document"]), number)
    image_token, thumb_token = token_for(t, "image"), token_for(t, "thumb")
    image, thumb = jpeg_of(image_token), jpeg_of(thumb_token)
    image_path = paths.page_image_path(t.case, page)
    await put_sealed(env, t.case, image_path, image)
    await put_sealed(env, t.case, paths.thumbnail_path(t.case, page), thumb)
    env.admin.execute("update app.pages set image_path = %s where id = %s", (image_path, page))
    return (
        Obj(f"/pages/{number}/image", image, (image_token.encode(),), "image/jpeg"),
        Obj(f"/pages/{number}/thumbnail", thumb, (thumb_token.encode(),), "image/jpeg"),
    )


async def seed_page_image(env: Env, t: Tenant) -> Obj:
    return (await seed_page(env, t))[0]


async def seed_page_thumbnail(env: Env, t: Tenant) -> Obj:
    return (await seed_page(env, t))[1]


async def seed_document_file(env: Env, t: Tenant) -> Obj:
    """A new document stored as one sealed part under its own prefix."""
    document = env.seed.document(t.case)
    token = token_for(t, "document")
    plain = f"ISO-DOCUMENT {token}".encode() + os.urandom(16)
    prefix = upload_prefix(t.case, uuid.uuid4())
    await put_sealed(env, t.case, f"{prefix}/00001", plain)
    env.admin.execute(
        "update app.documents set storage_path = %s, sha256 = %s where id = %s", (prefix, sha(plain), document)
    )
    # The download waits for a clean virus scan (423 file_not_ready), so the seeded file has one on record.
    env.admin.execute(
        "insert into app.document_scans (document_id, bundle_id, clean)"
        " values (%s, (select bundle_id from app.cases where id = %s), true)",
        (document, t.case),
    )
    return Obj(f"/documents/{document}/file", plain, (token.encode(),), "application/octet-stream")


async def seed_report(env: Env, t: Tenant) -> tuple[Obj, Obj, Obj]:
    """A new draft report version, the newest of its case, with a real PDF and a Word file: (docx, pdf, preview)."""
    from app.features.report.jobs import file_path
    from app.features.report.pdf_out import page_png

    version = add_report_version(env.admin, t.case, version_no=next(_COUNTER))
    pdf_token, docx_token = token_for(t, "report-pdf"), token_for(t, "report-docx")
    pdf = pdf_of(pdf_token)
    docx = zip_of(docx_token)
    pdf_path, docx_path = file_path(t.case, version, "report.pdf"), file_path(t.case, version, "report.docx")
    await put_sealed(env, t.case, pdf_path, pdf)
    await put_sealed(env, t.case, docx_path, docx)
    env.admin.execute(
        "update app.report_versions set pdf_path = %s, docx_path = %s where id = %s", (pdf_path, docx_path, version)
    )
    png = page_png(pdf, 1)
    assert png is not None
    word = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return (
        Obj(f"/report/versions/{version}/download", docx, (docx_token.encode(),), word, {"format": "docx"}),
        Obj(f"/report/versions/{version}/download", pdf, (pdf_token.encode(),), "application/pdf", {"format": "pdf"}),
        Obj("/report/preview/1", png, (), "image/png", has_id=False),
    )


async def seed_report_docx(env: Env, t: Tenant) -> Obj:
    return (await seed_report(env, t))[0]


async def seed_report_pdf(env: Env, t: Tenant) -> Obj:
    return (await seed_report(env, t))[1]


async def seed_report_preview(env: Env, t: Tenant) -> Obj:
    return (await seed_report(env, t))[2]


_INVOICES: dict[str, Obj] = {}


async def seed_invoice_pdf(env: Env, t: Tenant) -> Obj:
    """The tenant's seeded invoice, given a stored PDF (a sent invoice's `pdf_path` is set once, then never changes,
    so the same file serves every test of the tenant)."""
    invoice = t.ids["invoice"]
    if invoice not in _INVOICES:
        token = token_for(t, "invoice")
        pdf = pdf_of(token)
        path = f"{case_prefix(t.case)}/invoices/{invoice}/{uuid.uuid4().hex}.pdf"
        await put_sealed(env, t.case, path, pdf)
        env.admin.execute("update app.invoices set pdf_path = %s where id = %s", (path, invoice))
        _INVOICES[invoice] = Obj(f"/invoices/{invoice}/pdf", pdf, (token.encode(),), "application/pdf")
    return _INVOICES[invoice]


async def seed_export_zip(env: Env, t: Tenant) -> Obj:
    """A finished export job of the named expert, with its ZIP stored (it is handed over once, then removed)."""
    from app.features.close.service import export_path

    job = env.seed.job(t.case, kind="export_case", requested_by=t.expert)
    _finish(env.admin, job)
    token = token_for(t, "export")
    data = zip_of(token)
    await put_sealed(env, t.case, export_path(t.case, job, "full"), data)
    # PV-17b: the real export job records the archive's hash once it is stored (`close/jobs.py`), and a download whose
    # hash was not recorded is refused. The audit row is written the way the job writes it: no person, the case, and
    # the target `export:<job>:<variant>:<sha256>`.
    env.admin.execute(
        "insert into app.audit_log (person_id, organisation_id, case_id, action, target)"
        " values (null, null, %s, 'case.export_built', %s)",
        (t.case, f"export:{job}:full:{sha(data)}"),
    )
    return Obj(f"/export/{job}/download", data, (token.encode(),), "application/zip")


async def seed_transcript_file(env: Env, t: Tenant) -> Obj:
    """A finished transcript-file job of the named expert, with its Word file stored (handed over once)."""
    from app.features.reader.transcript_jobs import KIND, transcript_path

    job = env.seed.job(t.case, kind=KIND, requested_by=t.expert)
    _finish(env.admin, job)
    token = token_for(t, "transcript")
    data = zip_of(token)
    await put_sealed(env, t.case, transcript_path(t.case, job, "docx"), data)
    # PV-12/PV-16: the transcript job records the file's hash once it is stored, and a download with no hash row
    # is refused for jobs made after the hash was introduced.
    env.admin.execute(
        "insert into app.transcript_files (job_id, fmt, sha256) values (%s, 'docx', %s)", (job, sha(data))
    )
    return Obj(
        f"/transcript/download/{job}",
        data,
        (token.encode(),),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


async def seed_trial_pack(env: Env, t: Tenant) -> tuple[Obj, Obj]:
    """The tenant's seeded trial pack, given a stored PDF: (latest, download)."""
    from app.features.trial.common import trial_path

    token = token_for(t, "pack")
    pdf = pdf_of(token)
    path = trial_path(t.case, "packs", f"{uuid.uuid4()}.pdf")
    await put_sealed(env, t.case, path, pdf)
    env.admin.execute(
        "update app.trial_packs set storage_path = %s, sha256 = %s, page_count = 1 where case_id = %s",
        (path, sha(pdf), t.case),
    )
    return (
        Obj("/trial-pack/latest", b"", (sha(pdf).encode(),), "application/json", has_id=False),
        Obj("/trial-pack/latest/download", pdf, (token.encode(),), "application/pdf", has_id=False),
    )


async def seed_trial_pack_latest(env: Env, t: Tenant) -> Obj:
    return (await seed_trial_pack(env, t))[0]


async def seed_trial_pack_download(env: Env, t: Tenant) -> Obj:
    return (await seed_trial_pack(env, t))[1]


async def seed_concordance_csv(env: Env, t: Tenant) -> Obj:
    """The tenant's seeded concordance row, given a trial page number of its own."""
    from app.core.contracts.served import PARAGRAPH_PURPOSE

    number = next(_COUNTER)
    env.admin.execute("update app.concordance set page_no = %s where id = %s", (number, t.ids["concordance"]))
    sealed = await env.crypto.encrypt(
        env.aconn, t.case, b"zz paragraph", purpose=PARAGRAPH_PURPOSE
    )  # the served report's
    env.admin.execute(
        "update app.report_paragraphs set text = %s where report_version_id in"
        " (select id from app.report_versions where case_id = %s and status = 'served')",
        (sealed, t.case),
    )
    return Obj("/concordance/export", b"", (f"\n{number},".encode(),), "text/csv", {"format": "csv"}, has_id=False)


async def seed_offline_package(env: Env, t: Tenant) -> Obj:
    """A built offline copy (handed over once): its key sealed under the case key, its ciphertext stored."""
    from app.features.trial.common import offline_key_purpose, trial_path

    package = uuid.uuid4()
    key, blob = os.urandom(32), os.urandom(12) + os.urandom(48)
    path = trial_path(t.case, "offline", f"{package}.bin")
    await env.storage.put(path, blob)
    sealed_key = await env.crypto.encrypt(env.aconn, t.case, key, purpose=offline_key_purpose(package))
    env.admin.execute(
        "insert into app.offline_packages (id, case_id, report_version_id, device_label, format_version, storage_path,"
        " sha256, key_sealed, status, expires_at, created_by)"
        " values (%s, %s, %s, 'zz', 1, %s, %s, %s, 'built', %s, %s)",
        (
            package,
            t.case,
            t.ids["served_version"],
            path,
            sha(blob),
            sealed_key,
            datetime.now(UTC) + timedelta(days=1),
            t.expert,
        ),
    )
    key_b64, ct_b64 = base64.b64encode(key), base64.b64encode(blob[12:])
    return Obj(f"/offline-packages/{package}/download", b"", (key_b64, ct_b64), "application/json")


async def seed_certificate(env: Env, t: Tenant) -> Obj:
    """A finished deletion on a case of its own (a started deletion locks the case, so not the tenant's main one)."""
    case = env.seed.case(t.org, t.expert)
    number = next(_COUNTER)
    env.admin.execute(
        "insert into app.deletion_certificates (case_id, deleted_at, counts) values (%s, now(), %s)",
        (case, json.dumps({"pages": number})),
    )
    for role in ("assistant", "viewer"):
        env.seed.case_member(case, t.people[role], role, added_by=t.expert)
    return Obj(
        "/deletion-certificate",
        b"",
        (f'"pages":{number}'.encode(), f'"pages": {number}'.encode()),
        "application/json",
        {"format": "json"},
        case=case,
        has_id=False,
    )


async def seed_signature(env: Env, person: uuid.UUID) -> Obj:
    """A stored signature image of the person (the route is `/v1/me/signature/image`, no case in the path)."""
    sid = uuid.uuid4()
    token = f"ISO-SIGNATURE-{uuid.uuid4().hex}"
    png = b"\x89PNG\r\n\x1a\n" + token.encode()
    path = f"people/{person}/signature-{sid}"
    sealed = SecretBox(CRYPTO.provider).seal(
        base64.b64encode(png).decode(), purpose="signature", subject=f"{person}:{sid}"
    )
    await env.storage.put(path, sealed.encode())
    env.admin.execute(
        "insert into app.signatures (id, expert_id, method, storage_path, sha256, width, height)"
        " values (%s, %s, 'drawn', %s, %s, 10, 10)",
        (sid, person, path, sha(png)),
    )
    return Obj("/v1/me/signature/image", png, (token.encode(),), "image/png")


async def seed_spot_check_rows(env: Env, t: Tenant) -> None:
    """Real ciphertext in the rows a spot check opens (the cited values); the parties, the subject's identity, the
    page pictures and the OCR words, which it would also open, are removed (the parties through the purge path the
    database allows) so the POST can reach a 201."""
    from app.features.report.assemble import VALUE_PURPOSE

    bundle = "(select bundle_id from app.cases where id = %s)"
    env.admin.execute("select set_config('app.purging_case_id', %s, false)", (str(t.case),))
    try:
        env.admin.execute("delete from app.case_parties where case_id = %s", (t.case,))
        env.admin.execute("delete from app.case_subject_identity where case_id = %s", (t.case,))
    finally:
        env.admin.execute("select set_config('app.purging_case_id', '', false)")
    sealed = await env.crypto.encrypt(env.aconn, t.case, b"zz value", purpose=VALUE_PURPOSE)
    env.admin.execute("update app.citations set value_snapshot = %s where case_id = %s", (sealed, t.case))
    env.admin.execute(f"update app.pages set image_path = null where bundle_id = {bundle}", (t.case,))
    env.admin.execute(f"delete from app.ocr_words where bundle_id = {bundle}", (t.case,))


def world_tenants(world: IsolationWorld) -> tuple[Tenant, Tenant]:
    return world.victim, world.attacker
