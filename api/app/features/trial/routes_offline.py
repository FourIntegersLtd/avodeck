"""Screen 25's offline copy (rule 4's exception: the witness-box search runs on the device with no connection).

- `POST /cases/{c}/offline-packages` (`report.sign`, step-up): builds format version 1 from the served
  report, the verified facts with their old and trial page numbers, the concordance, the joint statement and
  the answers. Page images are never included. The JSON is sealed with a fresh AES-GCM key (AAD
  `avodeck-offline:{id}:{format_version}`); only the ciphertext is stored and the key is kept sealed by the
  case key.
- `GET .../{id}/download` (step-up): releases the key once, then forgets it (410 afterwards, or once
  revoked or expired).
- `DELETE .../{id}` (step-up): revokes, deletes the file and the key.
- `GET .../{id}/status`: the status alone (no case content), which screen 25 asks when online and wipes the
  copy on revoked, expired or 404. Case access but no step-up: the expert may be in court hours later.
"""

from __future__ import annotations

import base64
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.contracts.facts import citable_facts
from app.core.contracts.served import latest_served
from app.core.errors import ApiError
from app.core.http import DbConn
from app.features.trial import repo
from app.features.trial.common import (
    NO_STORE,
    Crypto,
    Pack,
    Sign,
    Storage,
    View,
    conflict,
    invalid,
    not_found,
    offline_key_purpose,
    sha256,
    trial_path,
)
from app.features.trial.ports import answers_of
from app.features.trial.router import router
from app.features.trial.services import current

FORMAT_VERSION = 1
Status = Literal["built", "downloaded", "revoked", "expired"]


class PackageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    device_label: str = Field(min_length=1, max_length=80)
    include_images: Literal[False] = False
    hearing_id: uuid.UUID | None = None
    days: int = Field(default=30, ge=1, le=90)


class PackageOut(BaseModel):
    id: uuid.UUID
    device_label: str
    hearing_id: uuid.UUID | None
    format_version: int
    include_images: bool
    sha256: str | None
    status: Status
    expires_at: datetime
    downloaded_at: datetime | None
    revoked_at: datetime | None
    created_at: datetime


class PackagesOut(BaseModel):
    packages: list[PackageOut]


class PackageStatus(BaseModel):
    status: Status


class Download(BaseModel):
    id: uuid.UUID
    case_id: uuid.UUID
    format_version: int
    key: str
    iv: str
    ciphertext: str


def aad(package_id: uuid.UUID, format_version: int) -> bytes:
    return f"avodeck-offline:{package_id}:{format_version}".encode()


def status_of(row: dict[str, Any]) -> Status:
    if row["status"] == "revoked":
        return "revoked"
    if row["expires_at"] <= datetime.now(UTC):
        return "expired"
    return row["status"]  # type: ignore[no-any-return]


def _out(row: dict[str, Any]) -> PackageOut:
    return PackageOut(
        id=row["id"],
        device_label=row["device_label"],
        hearing_id=row["hearing_id"],
        format_version=row["format_version"],
        include_images=row["include_images"],
        sha256=row["sha256"],
        status=status_of(row),
        expires_at=row["expires_at"],
        downloaded_at=row["downloaded_at"],
        revoked_at=row["revoked_at"],
        created_at=row["created_at"],
    )


async def content(conn: Any, request: Request, crypto: Any, pack: Any, case_id: uuid.UUID) -> dict[str, Any]:
    """Format version 1 of the offline copy (what S25 searches on the device)."""
    served = await latest_served(conn, crypto, case_id)
    if served is None:
        raise conflict("not_served", "The offline copy is made from the served report. Serve it first.")
    refs = await repo.page_refs(conn, case_id)
    reads = getattr(request.app.state, "page_reads", None) or current().reads
    facts = await citable_facts(conn, case_id, reads)
    numbers = await repo.page_numbers(conn, list({f.page_id for f in facts}))

    def trial_no(page_no: int | None) -> int | None:
        ref = refs.get(page_no) if page_no is not None else None
        return ref.trial_page_no if ref else None

    meeting = await repo.latest_meeting(conn, case_id)
    items = await repo.current_items(conn, crypto, case_id, meeting["id"]) if meeting else []
    answers = answers_of(await current().answers.served(conn, crypto, case_id))
    return {
        "format_version": FORMAT_VERSION,
        "case_id": str(case_id),
        "built_at": datetime.now(UTC).isoformat(),
        "date_order": pack.date_order,
        "court_bundle": pack.term("court_bundle"),
        "report": {
            "version_no": served.version_no,
            "served_on": served.served_on.isoformat() if served.served_on else None,
            "paragraphs": [
                {
                    "para_no": p.para_no,
                    "section": p.section,
                    "text": p.text,
                    "pages": [{"page_no": n, "trial_page_no": trial_no(n)} for _, n in p.cited],
                }
                for p in served.paragraphs
            ],
        },
        "facts": [
            {
                "kind": f.kind,
                "text": f.value or "",
                "date": f.event_date.isoformat() if f.event_date else None,
                "page_no": numbers.get(f.page_id),
                "trial_page_no": trial_no(numbers.get(f.page_id)),
            }
            for f in facts
        ],
        "concordance": [
            {"page_no": r.page_no, "trial_page_no": r.trial_page_no, "status": r.status} for r in refs.values()
        ],
        "joint_statement": {
            "type": meeting["type"],
            "status": meeting["status"],
            "items": [
                {
                    "number": i["number"],
                    "text": i["text"],
                    "own_position": i["own_position"],
                    "agreed": i["agreed"],
                    "reason": i["reason"],
                }
                for i in items
            ],
        }
        if meeting
        else None,
        "answers": [{"question_no": a.question_no, "text": a.text} for a in answers],
    }


@router.post("/cases/{case_id}/offline-packages", status_code=201, response_model=PackageOut)
async def create_package(
    case_id: uuid.UUID,
    body: PackageIn,
    request: Request,
    conn: DbConn,
    access: Sign,
    crypto: Crypto,
    storage: Storage,
    pack: Pack,
) -> PackageOut:
    if not await current().hearings.belongs(conn, case_id, body.hearing_id):
        raise invalid("hearing_id")
    package_id = uuid.uuid4()
    plain = json.dumps(await content(conn, request, crypto, pack, case_id), separators=(",", ":")).encode()
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    blob = nonce + AESGCM(key).encrypt(nonce, plain, aad(package_id, FORMAT_VERSION))
    path = trial_path(case_id, "offline", f"{package_id}.bin")
    await storage.put(path, blob)
    served = await latest_served(conn, crypto, case_id)
    row = await repo.one(
        conn,
        "insert into app.offline_packages (id, case_id, hearing_id, report_version_id, device_label, include_images,"
        " format_version, storage_path, sha256, key_sealed, expires_at)"
        " values (%s, %s, %s, %s, %s, false, %s, %s, %s, %s, %s) returning *",
        (
            package_id,
            case_id,
            body.hearing_id,
            served.version_id if served else None,
            body.device_label,
            FORMAT_VERSION,
            path,
            sha256(blob),
            await crypto.encrypt(conn, case_id, key, purpose=offline_key_purpose(package_id)),
            datetime.now(UTC) + timedelta(days=body.days),
        ),
    )
    assert row is not None
    await audit.record(conn, action="trial.offline_created", target=str(package_id), case_id=case_id)
    return _out(row)


@router.get("/cases/{case_id}/offline-packages", response_model=PackagesOut)
async def list_packages(case_id: uuid.UUID, conn: DbConn, access: View) -> PackagesOut:
    rows = await repo.many(
        conn, "select * from app.offline_packages where case_id = %s order by created_at desc", (case_id,)
    )
    return PackagesOut(packages=[_out(r) for r in rows])


async def _package(conn: Any, case_id: uuid.UUID, package_id: uuid.UUID, lock: bool = False) -> dict[str, Any]:
    row = await repo.one(
        conn,
        "select * from app.offline_packages where case_id = %s and id = %s" + (" for update" if lock else ""),
        (case_id, package_id),
    )
    if row is None:
        raise not_found()
    return row


@router.get("/cases/{case_id}/offline-packages/{package_id}", response_model=PackageOut)
async def get_package(case_id: uuid.UUID, package_id: uuid.UUID, conn: DbConn, access: View) -> PackageOut:
    return _out(await _package(conn, case_id, package_id))


@router.get("/cases/{case_id}/offline-packages/{package_id}/status", response_model=PackageStatus)
async def package_status(
    case_id: uuid.UUID, package_id: uuid.UUID, conn: DbConn, access: View, response: Response
) -> PackageStatus:
    response.headers.update(NO_STORE)
    return PackageStatus(status=status_of(await _package(conn, case_id, package_id)))


@router.get("/cases/{case_id}/offline-packages/{package_id}/download", response_model=Download)
async def download_package(
    case_id: uuid.UUID,
    package_id: uuid.UUID,
    conn: DbConn,
    access: Sign,
    crypto: Crypto,
    storage: Storage,
    response: Response,
) -> Download:
    row = await _package(conn, case_id, package_id, lock=True)
    status = status_of(row)
    if status == "downloaded":
        raise ApiError("already_downloaded", "This copy has already been downloaded. Make a new one.", 410)
    if status in ("revoked", "expired"):
        raise ApiError(status, "This copy can no longer be downloaded. Make a new one.", 410)
    key = await crypto.decrypt(conn, case_id, bytes(row["key_sealed"]), purpose=offline_key_purpose(package_id))
    blob = await storage.get(row["storage_path"])
    await conn.execute(
        "update app.offline_packages set status = 'downloaded', downloaded_at = now(), key_sealed = null where id = %s",
        (package_id,),
    )
    await audit.record(conn, action="trial.offline_downloaded", target=str(package_id), case_id=case_id)
    response.headers.update(NO_STORE)
    return Download(
        id=package_id,
        case_id=case_id,
        format_version=row["format_version"],
        key=base64.b64encode(key).decode(),
        iv=base64.b64encode(blob[:12]).decode(),
        ciphertext=base64.b64encode(blob[12:]).decode(),
    )


@router.delete("/cases/{case_id}/offline-packages/{package_id}", status_code=204)
async def delete_package(
    case_id: uuid.UUID, package_id: uuid.UUID, conn: DbConn, access: Sign, storage: Storage
) -> Response:
    row = await _package(conn, case_id, package_id, lock=True)
    if row["status"] != "revoked":
        await conn.execute(
            "update app.offline_packages set status = 'revoked', revoked_at = now(), key_sealed = null where id = %s",
            (package_id,),
        )
        if row["storage_path"]:
            await storage.delete([row["storage_path"]])
        await audit.record(conn, action="trial.offline_revoked", target=str(package_id), case_id=case_id)
    return Response(status_code=204)
