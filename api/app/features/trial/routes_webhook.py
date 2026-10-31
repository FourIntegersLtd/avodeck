"""The e-sign provider's webhook for joint statements, signed out (threat model: a new inbound flow).

`POST /v1/webhooks/esign` carries `x-avodeck-signature: sha256=<hex>`, an HMAC of the raw body with the
cell's `ESIGN_WEBHOOK_SECRET`, read through `core.config.webhook_settings` (401 for everything when the cell
has none). The body names the signing reference, never a case: migration 0900's security definer functions
find the meeting by the reference's hash, move it and audit with no person.
Unknown reference 404; a statement not out for signature 409 (a replay).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
from typing import Literal

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.core.config import webhook_settings
from app.core.db import Database
from app.core.errors import ApiError
from app.core.http import public_router

# The provider's server calls this, so it sends no browser `Origin`; the signature is its proof.
webhook_router = public_router(prefix="/v1", tags=["trial"], machine=True)

MAX_BODY = 512 * 1024


class EsignEvent(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reference: str = Field(min_length=16, max_length=128)
    event: Literal["signed", "declined"]
    signed_document_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    certificate: str | None = Field(default=None, max_length=400_000)


class EsignOutcome(BaseModel):
    status: Literal["signed", "declined"]


def _checked(body: bytes, header: str | None) -> None:
    """401 unless the body is signed with the cell's secret; a cell with no secret accepts nothing."""
    secret = webhook_settings().esign_secret
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest() if secret else None
    if not expected or not header or not hmac.compare_digest(expected, header.strip()):
        raise ApiError("bad_signature", "The signature does not match.", 401)


@webhook_router.post("/webhooks/esign", response_model=EsignOutcome)
async def esign_webhook(request: Request) -> EsignOutcome:
    body = await request.body()
    if len(body) > MAX_BODY:
        raise ApiError("invalid", "Too large.", 413)
    _checked(body, request.headers.get("x-avodeck-signature"))
    try:
        event = EsignEvent.model_validate_json(body)
    except ValidationError:
        raise ApiError("invalid", "Check the event.", 422) from None
    reference = hashlib.sha256(event.reference.encode()).hexdigest()
    database: Database = request.app.state.database
    async with database.anonymous() as conn:
        if event.event == "signed":
            if not event.signed_document_sha256 or not event.certificate:
                raise ApiError("invalid", "Check the event.", 422)
            try:
                certificate = base64.b64decode(event.certificate, validate=True)
            except binascii.Error:
                raise ApiError("invalid", "Check the event.", 422) from None
            cur = await conn.execute(
                "select app.trial_esign_signed(%s, %s, %s)", (reference, event.signed_document_sha256, certificate)
            )
        else:
            cur = await conn.execute("select app.trial_esign_declined(%s)", (reference,))
        row = await cur.fetchone()
    outcome = str(row[0]) if row else "unknown"
    if outcome == "unknown":
        raise ApiError("not_found", "Not found.", 404)
    if outcome == "not_out":
        raise ApiError("conflict", "This statement is not out for signature.", 409)
    return EsignOutcome(status="signed" if outcome == "signed" else "declined")
