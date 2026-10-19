"""What the expert prepares for the day (screens 23 and 24).

- `PUT /cases/{c}/readiness-replies/{question_no}` (`opinion.write`): the expert's own reply to one question
  of the current agenda, sealed under the case key. It prints on the question's readiness sheet.
- `GET /cases/{c}/trial-pack/notes` (`case.view_content`) and `PUT /cases/{c}/trial-pack/notes/{note_id}`
  (`report.build`): the sentences of a new note found in no served document (kept by `check-new`) and the
  expert's choice about each, to tell the instructing party or to leave it out. The choice is only recorded.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import Response
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.http import DbConn
from app.features.trial import repo
from app.features.trial.common import READINESS_REPLY, Build, Crypto, View, Write, invalid, not_found, seal
from app.features.trial.router import router
from app.features.trial.services import current


class ReplyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=8_000)


class ReplyOut(BaseModel):
    question_no: int
    reply: str
    replied_at: datetime


class NoteChoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    choice: Literal["tell", "leave_out"]


class NoteOut(BaseModel):
    id: uuid.UUID
    text: str
    choice: Literal["tell", "leave_out"] | None
    decided_at: datetime | None


class NotesOut(BaseModel):
    notes: list[NoteOut]


def _note(row: dict[str, object]) -> NoteOut:
    return NoteOut(id=row["id"], text=row["text"], choice=row["choice"], decided_at=row["decided_at"])  # type: ignore[arg-type]


@router.put("/cases/{case_id}/readiness-replies/{question_no}", response_model=ReplyOut)
async def put_reply(
    case_id: uuid.UUID, question_no: int, body: ReplyIn, conn: DbConn, access: Write, crypto: Crypto
) -> ReplyOut:
    meeting = await repo.latest_meeting(conn, case_id)
    items = await repo.current_items(conn, current().case_crypto(), case_id, meeting["id"]) if meeting else []
    if question_no not in {i["number"] for i in items}:
        raise invalid("question_no")
    row = await repo.one(
        conn,
        "insert into app.readiness_replies (case_id, question_no, reply) values (%s, %s, %s)"
        " on conflict (case_id, question_no) do update set reply = excluded.reply, replied_at = now(),"
        " replied_by = app.current_person_id() returning replied_at",
        (case_id, question_no, await seal(conn, crypto, case_id, body.reply, READINESS_REPLY)),
    )
    assert row is not None
    await audit.record(conn, action="trial.reply_saved", target=str(question_no), case_id=case_id)
    return ReplyOut(question_no=question_no, reply=body.reply, replied_at=row["replied_at"])


@router.get("/cases/{case_id}/trial-pack/notes", response_model=NotesOut)
async def list_notes(case_id: uuid.UUID, conn: DbConn, access: View, crypto: Crypto, response: Response) -> NotesOut:
    response.headers["Cache-Control"] = "private, no-store"
    return NotesOut(notes=[_note(r) for r in await repo.notes(conn, crypto, case_id)])


@router.put("/cases/{case_id}/trial-pack/notes/{note_id}", response_model=NoteOut)
async def choose_for_note(
    case_id: uuid.UUID, note_id: uuid.UUID, body: NoteChoiceIn, conn: DbConn, access: Build, crypto: Crypto
) -> NoteOut:
    done = await repo.one(
        conn,
        "update app.trial_notes set choice = %s, decided_at = now(), decided_by = app.current_person_id()"
        " where case_id = %s and id = %s returning id",
        (body.choice, case_id, note_id),
    )
    if done is None:
        raise not_found()
    await audit.record(conn, action=f"trial.note_{body.choice}", target=str(note_id), case_id=case_id)
    [row] = [r for r in await repo.notes(conn, crypto, case_id) if r["id"] == note_id]
    return _note(row)
