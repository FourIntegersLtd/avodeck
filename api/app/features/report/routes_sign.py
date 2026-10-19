"""Screen 18: sign the report, download a version, and see what a signed version was signed with.

`POST .../report/sign` (named expert, step-up) signs the case's latest draft: its latest final check must
be for that draft and have passed (409 `final_check_needed` / `final_check_failed`), every citation must
still stand (409 `citation_problems`), any corrected declaration must be acknowledged (409
`declaration_not_acknowledged`, F34) and the expert must confirm the declaration (422). Signing adds the
signature, name and date to both files, stores each file's SHA-256 and the frozen snapshots, locks the
version under the pack's document name ("Report 1"), records the acknowledged declaration and writes to
the audit log. Downloads stream with `no-store`, are audited, and a locked version's file must match its
stored hash (409 `hash_mismatch`).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict

from app.core import audit
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
from app.core.contracts import profile
from app.core.contracts.citations import citation_problems
from app.core.errors import ApiError
from app.core.http import DbConn
from app.features.report.checks import declaration_version, named_expert
from app.features.report.common import Crypto, Pack, Reads, Sign, Storage, View, not_found, refuse_if_served
from app.features.report.jobs import file_path, latest_draft
from app.features.report.router import router
from app.features.report.signing import (
    canonical,
    frozen,
    open_signature,
    sha256,
    snapshot_purpose,
    stamp_docx,
    stamp_pdf,
    version_hash,
)
from app.features.report.words import long_date, word

NO_STORE = {"Cache-Control": "private, no-store"}
MEDIA = {
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
FILE_KINDS = ("docx", "pdf")


class SignIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    declaration_confirmed: Literal[True]


class SignedVersion(BaseModel):
    id: uuid.UUID
    version_no: int
    display_name: str
    status: str
    locked_at: datetime
    sha256: str
    docx_sha256: str
    pdf_sha256: str


class SignedOut(BaseModel):
    version: SignedVersion


class SnapshotOut(BaseModel):
    kind: str
    sha256: str
    content: Any = None
    created_at: datetime


class SnapshotsOut(BaseModel):
    snapshots: list[SnapshotOut]


def conflict(code: str, message: str) -> ApiError:
    return ApiError(code, message, 409)


async def _final_check_gate(conn: Any, case_id: uuid.UUID, version_id: uuid.UUID) -> None:
    cur = await conn.execute(
        "select report_version_id, passed from app.final_check_runs where case_id = %s"
        " order by at desc, created_at desc limit 1",
        (case_id,),
    )
    run = await cur.fetchone()
    if run is None or run[0] != version_id:
        raise conflict("final_check_needed", "Run the final check on the latest draft first.")
    if not run[1]:
        raise conflict("final_check_failed", "Fix what the final check found first.")


async def _signed_name(conn: Any, case_id: uuid.UUID, kind: str, language: str) -> str:
    cur = await conn.execute(
        "select count(*) from app.report_versions where case_id = %s and type = %s and locked_at is not null",
        (case_id, kind),
    )
    row = await cur.fetchone()
    n = int(row[0]) + 1 if row else 1
    return word(language, "supplementary" if kind == "supplementary" else "report", n=n)


@router.post("/cases/{case_id}/report/sign", response_model=SignedOut)
async def sign_report(
    case_id: uuid.UUID,
    body: SignIn,
    access: Sign,
    conn: DbConn,
    crypto: Crypto,
    storage: Storage,
    reads: Reads,
    pack: Pack,
) -> SignedOut:
    await refuse_if_served(conn, case_id)
    version_id = await latest_draft(conn, case_id)
    if version_id is None:
        raise conflict("not_built", "Build the report first.")
    # Lock the draft: a second sign waits here, then finds it no longer a draft.
    cur = await conn.execute(
        "select version_no, type, docx_path, pdf_path from app.report_versions"
        " where id = %s and status = 'draft' for update",
        (version_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise conflict("not_built", "Build the report first.")
    await _final_check_gate(conn, case_id, version_id)
    if await citation_problems(conn, case_id, reads):
        raise conflict("citation_problems", "A cited fact has changed since the final check.")
    cur = await conn.execute(
        "select acknowledged_declaration_version from app.report_settings where case_id = %s", (case_id,)
    )
    ack = await cur.fetchone()
    current = declaration_version(pack)
    if ack is not None and ack[0] is not None and ack[0] != current:
        raise conflict("declaration_not_acknowledged", "Read the corrected declaration first.")

    signature = await profile.signature(conn, case_id)
    expert = await named_expert(conn, case_id)
    if signature is None or expert is None:
        raise conflict("signature_unavailable", "Add your signature first.")
    try:
        image = open_signature(
            crypto.provider, await storage.get(signature.storage_path), expert["person_id"], signature.id
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable signature cannot be signed with
        raise conflict("signature_unavailable", "Your signature could not be opened.") from exc
    if sha256(image) != signature.sha256:
        raise conflict("signature_unavailable", "Your signature could not be opened.")

    version_no, kind, docx_draft, pdf_draft = row
    today = datetime.now(ZoneInfo(pack.timezone)).date()
    lines = [
        str(expert["full_name"] or ""),
        word(pack.language, "signed_on", date=long_date(pack.language, pack.date_order, today)),
    ]
    files: dict[str, bytes] = {}
    for fmt, draft in (("docx", docx_draft), ("pdf", pdf_draft)):
        data = await crypto.decrypt(conn, case_id, await storage.get(draft), purpose=draft)
        files[fmt] = stamp_docx(data, image, lines) if fmt == "docx" else stamp_pdf(data, image, lines)
    attempt = uuid.uuid4().hex  # each attempt writes its own files; the row records the ones that won
    paths = {fmt: file_path(case_id, version_id, f"signed-{attempt}.{fmt}") for fmt in FILE_KINDS}
    hashes = {fmt: sha256(files[fmt]) for fmt in FILE_KINDS}
    for fmt in FILE_KINDS:
        await storage.put(paths[fmt], await crypto.encrypt(conn, case_id, files[fmt], purpose=paths[fmt]))
        await conn.execute(
            "insert into app.report_snapshots (case_id, report_version_id, kind, content_ref, sha256)"
            " values (%s, %s, %s, %s, %s)",
            (case_id, version_id, fmt, paths[fmt], hashes[fmt]),
        )
    for snap_kind, content in (await frozen(conn, case_id, pack, signature)).items():
        plain = canonical(content)
        sealed = await crypto.encrypt(conn, case_id, plain, purpose=snapshot_purpose(snap_kind))
        await conn.execute(
            "insert into app.report_snapshots (case_id, report_version_id, kind, content, sha256)"
            " values (%s, %s, %s, %s, %s)",
            (case_id, version_id, snap_kind, sealed, sha256(plain)),
        )
    name = await _signed_name(conn, case_id, kind, pack.language)
    cur = await conn.execute(
        "update app.report_versions set status = 'locked', display_name = %s, docx_path = %s, pdf_path = %s,"
        " sha256 = %s where id = %s returning locked_at, sha256",
        (name, paths["docx"], paths["pdf"], version_hash(hashes["docx"], hashes["pdf"]), version_id),
    )
    locked = await cur.fetchone()
    assert locked is not None
    await conn.execute(
        "insert into app.report_settings (case_id, acknowledged_declaration_version, version) values (%s, %s, 2)"
        " on conflict (case_id) do update set"
        " acknowledged_declaration_version = excluded.acknowledged_declaration_version,"
        " version = app.report_settings.version + 1, updated_at = now()",
        (case_id, current),
    )
    await audit.record(conn, action="report.signed", target=f"report_version:{version_id}", case_id=case_id)
    return SignedOut(
        version=SignedVersion(
            id=version_id,
            version_no=version_no,
            display_name=name,
            status="locked",
            locked_at=locked[0],
            sha256=locked[1],
            docx_sha256=hashes["docx"],
            pdf_sha256=hashes["pdf"],
        )
    )


@router.get(
    "/cases/{case_id}/report/versions/{version_id}/download",
    response_class=Response,
    responses={200: {"content": {MEDIA["docx"]: {}, MEDIA["pdf"]: {}}}},
)
async def download_version(
    case_id: uuid.UUID,
    version_id: uuid.UUID,
    format: Literal["docx", "pdf"],
    request: Request,
    access: View,
    conn: DbConn,
    crypto: Crypto,
    storage: Storage,
) -> Response:
    cur = await conn.execute(
        "select docx_path, pdf_path, locked_at from app.report_versions where id = %s and case_id = %s",
        (version_id, case_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    path = row[0] if format == "docx" else row[1]
    if path is None:
        raise not_found()
    if row[2] is not None:
        check_step_up(current_session(request))  # a signed report leaving Avodeck is an export (rule 10)
    data = await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path)
    if row[2] is not None:
        cur = await conn.execute(
            "select sha256 from app.report_snapshots where report_version_id = %s and kind = %s",
            (version_id, format),
        )
        stored = await cur.fetchone()
        if stored is None or stored[0] != sha256(data):
            raise conflict("hash_mismatch", "This file does not match the signed version.")
    await audit.record(conn, action="report.downloaded", target=f"report_version:{version_id}", case_id=case_id)
    return Response(content=data, media_type=MEDIA[format], headers={**NO_STORE, "X-Content-SHA256": sha256(data)})


@router.get("/cases/{case_id}/report/versions/{version_id}/snapshots", response_model=SnapshotsOut)
async def version_snapshots(
    case_id: uuid.UUID, version_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto
) -> SnapshotsOut:
    cur = await conn.execute("select 1 from app.report_versions where id = %s and case_id = %s", (version_id, case_id))
    if await cur.fetchone() is None:
        raise not_found()
    cur = await conn.execute(
        "select kind, content, sha256, created_at from app.report_snapshots where report_version_id = %s order by kind",
        (version_id,),
    )
    out: list[SnapshotOut] = []
    for kind, content, digest, at in await cur.fetchall():
        shown = None
        if content is not None:
            plain = await crypto.decrypt(conn, case_id, bytes(content), purpose=snapshot_purpose(kind))
            shown = json.loads(plain)
        out.append(SnapshotOut(kind=kind, sha256=digest, content=shown, created_at=at))
    return SnapshotsOut(snapshots=out)
