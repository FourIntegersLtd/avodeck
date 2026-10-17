"""Dictation: the speech relay and saved transcripts (architecture 5.3 T-DICT, rule 4).

WS /v1/cases/{case_id}/dictation?context=<code>: the browser sends microphone audio as binary
frames and then `{"type": "end"}`; FastAPI relays the audio to the cell's speech service with the
case's pack locale and phrase list, and sends back `{"type": "segment", "text", "start_ms", "end_ms"}`
messages and `{"type": "done"}`. No audio is stored. The speech service's endpoint and key stay here.
If the speech service cannot take the audio the browser gets `{"type": "error", "code":
"speech_unavailable"}` and the connection closes with 1011; the expert types instead.

POST /v1/cases/{case_id}/transcripts saves the finished, edited text, encrypted under the case's key.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from app.core import audit
from app.core.auth.access import (
    CaseAccess,
    SocketCaseAccess,
    require_case_access,
    require_socket_case_access,
)
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import DbConn, router

logger = logging.getLogger("app.dictation")

DICTATION_ACTION = "comment.write"  # everyone who may write on the case may dictate

dictation_socket = APIRouter(tags=["dictation"])
transcripts_router = router(prefix="/v1/cases", tags=["dictation"])


class _Ended(Exception):
    """The browser went away before it finished."""


async def _audio(websocket: WebSocket) -> AsyncIterator[bytes]:
    """Binary frames until `{"type": "end"}`. Held in memory only, never written anywhere."""
    while True:
        message = await websocket.receive()
        if message["type"] == "websocket.disconnect":
            raise _Ended
        data = message.get("bytes")
        if data is not None:
            yield data
            continue
        text = message.get("text")
        if text is not None:
            try:
                parsed = json.loads(text)
            except ValueError:
                continue
            if isinstance(parsed, dict) and parsed.get("type") == "end":
                return


@dictation_socket.websocket("/v1/cases/{case_id}/dictation")
async def dictation(
    websocket: WebSocket,
    allowed: Annotated[SocketCaseAccess, Depends(require_socket_case_access(DICTATION_ACTION))],
) -> None:
    speech = websocket.app.state.speech
    jurisdiction, case_type, specialty = allowed.case
    settings = websocket.app.state.packs.combine(jurisdiction, case_type, specialty).speech()
    await websocket.accept()
    try:
        if speech is None:
            raise RuntimeError("no speech service in this cell")
        async for segment in speech.transcribe(
            _audio(websocket), locale=settings["locale"], phrases=settings["phrases"]
        ):
            await websocket.send_json(
                {"type": "segment", "text": segment.text, "start_ms": segment.start_ms, "end_ms": segment.end_ms}
            )
    except (_Ended, WebSocketDisconnect):
        return
    except Exception as exc:  # noqa: BLE001 - the type only: messages may carry what was said (rule 6)
        logger.warning("dictation relay failed: %s", type(exc).__name__)
        await websocket.send_json({"type": "error", "code": "speech_unavailable"})
        await websocket.close(code=1011)
        return
    await websocket.send_json({"type": "done"})
    await websocket.close(code=1000)


class TranscriptIn(BaseModel):
    context: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$", description="Where the words go, e.g. a section key.")
    text: str = Field(min_length=1, max_length=200_000)
    duration_seconds: int = Field(ge=0, le=86_400)


class TranscriptSaved(BaseModel):
    id: uuid.UUID


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "Saving is not ready.", 503)
    return crypto


@transcripts_router.post("/{case_id}/transcripts", status_code=201, response_model=TranscriptSaved)
async def save_transcript(
    case_id: uuid.UUID,
    body: TranscriptIn,
    access: Annotated[CaseAccess, Depends(require_case_access(DICTATION_ACTION))],
    conn: DbConn,
    crypto: Annotated[CaseCrypto, Depends(_crypto)],
) -> TranscriptSaved:
    sealed = await crypto.encrypt(conn, case_id, body.text.encode())
    cur = await conn.execute(
        "insert into app.transcripts (case_id, person_id, context, text, duration_seconds)"
        " values (%s, app.current_person_id(), %s, %s, %s) returning id",
        (case_id, body.context, sealed, body.duration_seconds),
    )
    row = await cur.fetchone()
    assert row is not None
    await audit.record(conn, action="transcript.saved", target=f"transcript:{row[0]}", case_id=case_id)
    return TranscriptSaved(id=row[0])
