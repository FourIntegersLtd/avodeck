"""Questions asked (architecture 4): slice C extracts and confirms them; D, E and F read them
through `confirmed_questions(conn, case_id)`.

`text_verbatim` is the exact wording and never changes; an edited question keeps it and carries
`confirmed_text` (state-machines.md "Question").

The set is locked when the case's latest `instruction_versions` row that can carry questions (any kind
but `oral`) has `locked_at` (migration 0019):
`questions_locked()` answers that, and `require_questions_locked()` is the route dependency that
answers 409 `questions_not_confirmed` until then.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from fastapi import Request
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth.access import CaseAccess, check_case_access
from app.core.auth.permissions import CASE_ACTIONS
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
from app.core.errors import ApiError
from app.core.http import DbConn

QuestionStatus = Literal["suggested", "confirmed", "edited"]


class Question(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: uuid.UUID
    case_id: uuid.UUID
    instruction_version_id: uuid.UUID | None = None
    number: int
    text_verbatim: str
    source_word_ids: list[str] = Field(default_factory=list)
    para_ref: str | None = None
    kind: str
    outside_expertise: bool = False
    status: QuestionStatus
    confirmed_text: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime

    @property
    def wording(self) -> str:
        """The words to use: the confirmed text of an edited question, otherwise the exact words."""
        return self.confirmed_text if self.confirmed_text is not None else self.text_verbatim


_QUESTION_COLUMNS = (
    "id, case_id, instruction_version_id, number, text_verbatim, source_word_ids, para_ref, kind,"
    " outside_expertise, status, confirmed_text, version, created_at, updated_at"
)


async def confirmed_questions(conn: AsyncConnection, case_id: uuid.UUID) -> list[Question]:
    """The case's confirmed and edited questions in question order (number, then id).

    Runs as the caller under row-level security, so someone who cannot see the content gets none."""
    cur = await conn.execute(
        f"select {_QUESTION_COLUMNS} from app.questions"
        " where case_id = %s and status in ('confirmed', 'edited') order by number, id",
        (case_id,),
    )
    names = [c.name for c in cur.description or []]
    return [Question.model_validate(dict(zip(names, row, strict=True))) for row in await cur.fetchall()]


# --- the question-set lock (slice C locks; D, E and F wait for it) ------------------------------------


async def questions_locked(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    """Whether the case's questions are locked: its latest instruction version that can carry questions
    has `locked_at` set. Oral instructions (kind `oral`) carry no questions and are skipped.

    No such version yet, or a newer unlocked one (new instructions reopen the questions): False.
    Runs as the caller under row-level security, so someone who cannot see the content gets False.
    """
    cur = await conn.execute(
        "select locked_at is not null from app.instruction_versions where case_id = %s and kind <> 'oral'"
        " order by version_no desc limit 1",
        (case_id,),
    )
    row = await cur.fetchone()
    return bool(row and row[0])


def questions_not_confirmed() -> ApiError:
    return ApiError("questions_not_confirmed", "Confirm the questions first.", 409)


class QuestionsLockedGate:
    """A FastAPI dependency: the case access check for `action`, then 409 `questions_not_confirmed`
    until the questions are locked. `case_action` and `step_up` let the route scanners see it."""

    def __init__(self, action: str) -> None:
        if action not in CASE_ACTIONS:
            raise ValueError(f"unknown case action {action!r}: add it to docs/spec/permissions.md first")
        self.case_action = action
        self.step_up = CASE_ACTIONS[action].step_up

    async def __call__(self, request: Request, case_id: uuid.UUID, conn: DbConn) -> CaseAccess:
        access = await check_case_access(conn, case_id, self.case_action)
        if self.step_up:
            check_step_up(current_session(request))
        if not await questions_locked(conn, case_id):
            raise questions_not_confirmed()
        return access


def require_questions_locked(action: str = "case.view_content") -> QuestionsLockedGate:
    """Use as `access: Annotated[CaseAccess, Depends(require_questions_locked("opinion.write"))]`."""
    return QuestionsLockedGate(action)
