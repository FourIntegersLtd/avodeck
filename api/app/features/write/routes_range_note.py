"""Screen 14: the range note, the expert's own words on what other practitioners might reasonably do.

The heading comes from the case type (`pack.range_note_prompt()`); the text is stored encrypted under
the case key. Saves take If-Match with the note's version (`"0"` before the first save).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import Request, Response
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, Field

from app.core.http import DbConn, IfMatch, etag, stale
from app.features.write.common import Crypto, Write, case_pack, require_question
from app.features.write.router import router

TEXT = "range_note"


class RangeNote(BaseModel):
    prompt: str
    text: str | None
    version: int
    dictated_on: date | None = None


class RangeNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(max_length=20_000)


async def _current(conn: DbConn, case_id: uuid.UUID, question_id: uuid.UUID) -> tuple[bytes, int, date] | None:
    cur = await conn.execute(
        "select text, version, (created_at at time zone 'utc')::date from app.range_notes"
        " where case_id = %s and question_id = %s for update",
        (case_id, question_id),
    )
    row = await cur.fetchone()
    return None if row is None else (bytes(row[0]), row[1], row[2])


async def range_note(
    conn: DbConn, crypto: Crypto, case_id: uuid.UUID, question_id: uuid.UUID, prompt: str
) -> RangeNote:
    current = await _current(conn, case_id, question_id)
    if current is None:
        return RangeNote(prompt=prompt, text=None, version=0)
    text = (await crypto.decrypt(conn, case_id, current[0], purpose=TEXT)).decode()
    return RangeNote(prompt=prompt, text=text, version=current[1], dictated_on=current[2])


@router.put("/cases/{case_id}/questions/{question_id}/range-note", response_model=RangeNote)
async def save_range_note(
    case_id: uuid.UUID,
    question_id: uuid.UUID,
    saving: RangeNoteIn,
    if_match: IfMatch,
    request: Request,
    response: Response,
    access: Write,
    conn: DbConn,
    crypto: Crypto,
) -> RangeNote:
    """Save the expert's range note."""
    await require_question(conn, case_id, question_id)
    current = await _current(conn, case_id, question_id)
    if (0 if current is None else current[1]) != if_match:
        raise stale()
    blob = await crypto.encrypt(conn, case_id, saving.text.encode(), purpose=TEXT)
    try:
        async with conn.transaction():
            if current is None:
                cur = await conn.execute(
                    "insert into app.range_notes (case_id, question_id, text) values (%s, %s, %s) returning version",
                    (case_id, question_id, blob),
                )
            else:
                cur = await conn.execute(
                    "update app.range_notes set text = %s where case_id = %s and question_id = %s returning version",
                    (blob, case_id, question_id),
                )
    except UniqueViolation:
        raise stale() from None
    row = await cur.fetchone()
    assert row is not None
    response.headers["ETag"] = etag(row[0])
    pack = await case_pack(request, conn, case_id)
    return await range_note(conn, crypto, case_id, question_id, pack.range_note_prompt())
