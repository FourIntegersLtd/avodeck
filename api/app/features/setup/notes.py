"""Draft notes to the instructing parties (scenario S4: a joint expert's notes go to every current one).

A draft is returned, never sent: the expert reads and sends it. Its wording is i18n keys plus the
items it is about (question numbers and their exact words), so no country's words are written here
(rule 11). Recipients are every instructing party without an end date.
"""

from __future__ import annotations

import uuid
from typing import Literal

from psycopg import AsyncConnection
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.core import audit

NoteTopic = Literal["outside_expertise", "questions_reopened", "conflict_declared", "withdrawal"]


class Recipient(BaseModel):
    id: uuid.UUID
    type_key: str
    name: str
    firm: str
    email: str | None


class NoteItem(BaseModel):
    question_id: uuid.UUID | None = None
    number: int | None = None
    para_ref: str | None = None
    text: str | None = Field(default=None, description="The question's exact words, or the expert's own detail.")
    label_key: str | None = Field(default=None, description="i18n key for an item that is not a question.")


class NoteDraft(BaseModel):
    topic: NoteTopic
    subject_key: str
    body_key: str
    joint: bool
    recipients: list[Recipient]
    items: list[NoteItem]


async def recipients(conn: AsyncConnection, case_id: uuid.UUID) -> list[Recipient]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, type_key, name, firm, email from app.case_instructing_parties"
            " where case_id = %s and effective_to is null order by effective_from, created_at, id",
            (case_id,),
        )
        return [Recipient(**r) for r in await cur.fetchall()]


async def draft(conn: AsyncConnection, case_id: uuid.UUID, topic: NoteTopic, items: list[NoteItem]) -> NoteDraft:
    to = await recipients(conn, case_id)
    await audit.record(conn, action="case.note_drafted", target=f"case:{case_id}", case_id=case_id)
    return NoteDraft(
        topic=topic,
        subject_key=f"setup.notes.{topic}.subject",
        body_key=f"setup.notes.{topic}.body",
        joint=len(to) > 1,
        recipients=to,
        items=items,
    )
