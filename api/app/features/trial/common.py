"""Small helpers shared by slice I's routes and jobs: access, the case's packs, sealing case text, files."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, Request
from psycopg import AsyncConnection
from pydantic import BaseModel

from app.core import rate_limits
from app.core.auth.access import CaseAccess, require_case_access
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.hashing import sha256_hex
from app.core.http import DbConn
from app.core.packs import MergedPack, PackLibrary
from app.core.request_body import read_capped
from app.core.storage import CaseFileStorage, case_prefix

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Setup = Annotated[CaseAccess, Depends(require_case_access("case.edit_setup"))]
Upload = Annotated[CaseAccess, Depends(require_case_access("case.upload"))]
Write = Annotated[CaseAccess, Depends(require_case_access("opinion.write"))]
Verify = Annotated[CaseAccess, Depends(require_case_access("fact.verify"))]
Build = Annotated[CaseAccess, Depends(require_case_access("report.build"))]
Sign = Annotated[CaseAccess, Depends(require_case_access("report.sign"))]
Draft = Annotated[CaseAccess, Depends(require_case_access("request.draft"))]

# The routes that ask a model (T-22a on upload, T-22c on a changed position, T-24 on a new note): a person may
# make this many requests a minute to each (the lesser of this and the app-wide limit; off where that is off).
MODEL_CALLS_PER_MINUTE = 30


async def limit_model_calls(request: Request, conn: DbConn) -> None:
    """429 `slow_down` when this person has made `MODEL_CALLS_PER_MINUTE` requests to this route this minute."""
    default = int(getattr(request.app.state, "api_rate_per_minute", 0))
    if default <= 0:
        return
    route = request.scope["route"]
    await rate_limits.check(conn, f"{request.method} {route.path_format}", min(MODEL_CALLS_PER_MINUTE, default))


ModelCalls = Annotated[None, Depends(limit_model_calls)]  # put after the access check: a stranger still gets 404

NO_STORE = {"Cache-Control": "private, no-store"}
MAX_PDF_BYTES = 1024 * 1024 * 1024
REASON = r"^[a-z][a-z0-9_]{0,39}$"

# purposes the case key seals slice I's text under
AGENDA_TEXT = "trial_agenda_text"
POSITION = "trial_position"
OTHER_POSITIONS = "trial_other_positions"
AGREED_REASON = "trial_agreed_reason"
CHANGE_NOTE = "trial_change_note"
READINESS_REPLY = "trial_readiness_reply"
NEW_NOTE = "trial_new_note"


def offline_key_purpose(package_id: uuid.UUID) -> str:
    return f"trial_offline_key/{package_id}"


def not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


def invalid(*fields: str) -> ApiError:
    return ApiError("invalid", "Check the highlighted fields.", 422, fields=list(fields))


def conflict(code: str, message: str, **extra: object) -> ApiError:
    return ApiError(code, message, 409, **extra)


class Queued(BaseModel):
    job_id: uuid.UUID


sha256 = sha256_hex  # the name the other trial modules import


def trial_path(case_id: uuid.UUID, *parts: object) -> str:
    return "/".join([case_prefix(case_id), "trial", *(str(p) for p in parts)])


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "Saving is not ready.", 503)
    return crypto


def _storage(request: Request) -> CaseFileStorage:
    storage: CaseFileStorage | None = request.app.state.storage
    if storage is None:
        raise ApiError("unavailable", "Files are not ready.", 503)
    return storage


Crypto = Annotated[CaseCrypto, Depends(_crypto)]
Storage = Annotated[CaseFileStorage, Depends(_storage)]


async def pack_for(conn: AsyncConnection, library: PackLibrary, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    return library.combine(str(row[0]), str(row[1]), str(row[2]))


async def case_pack(request: Request, conn: DbConn, case_id: uuid.UUID) -> MergedPack:
    return await pack_for(conn, request.app.state.packs, case_id)


Pack = Annotated[MergedPack, Depends(case_pack)]


async def pdf_body(request: Request) -> bytes:
    """The raw PDF a route was sent (422 when it is not one)."""
    data = await read_capped(request, MAX_PDF_BYTES, too_large=lambda: invalid("file"))
    if not data.startswith(b"%PDF"):
        raise invalid("file")
    return data


async def seal(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, text: str | None, purpose: str) -> Any:
    if text is None:
        return None
    return await crypto.encrypt(conn, case_id, text.encode(), purpose=purpose)


async def unseal(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, blob: Any, purpose: str) -> str | None:
    if blob is None:
        return None
    return (await crypto.decrypt(conn, case_id, bytes(blob), purpose=purpose)).decode()


def meeting_type(pack: MergedPack, key: str) -> dict[str, Any] | None:
    return next((t for t in pack.meeting_types() if t.get("key") == key), None)
