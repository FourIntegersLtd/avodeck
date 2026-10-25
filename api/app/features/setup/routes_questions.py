"""Screen 06: what you've been asked (architecture 5.2, slice C brief, scenario S7).

The questions and assumptions found in the letter (`extract_questions`), each with its exact words.
Only the named expert confirms, edits or locks them (`question.confirm`). An edit keeps the exact
words and adds the expert's own (`confirmed_text`). "Confirm all" locks the set; D, E and F wait for
that lock through `require_questions_locked` (409 `questions_not_confirmed` until then). A newer letter
with a new question reopens a confirmed set and says so; so does a letter recorded without its file.
Oral instructions carry no questions: they never reopen or hide the set. When reading a letter fails the
screen says so and `read-again` queues it again (an expert or an assistant, `case.upload`). Notes to the
instructing parties are drafted for every current one (S4).
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Any, Literal

from fastapi import Depends, Query, Request, Response
from psycopg.errors import RaiseException
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.setup.common import CODE, invalid, not_found
from app.features.setup.letter import short_title
from app.features.setup.notes import NoteDraft, NoteItem, NoteTopic, draft
from app.features.setup.router import router
from app.features.setup.routes_case import ViewContent, _pack, conflict

Confirm = Annotated[CaseAccess, Depends(require_case_access("question.confirm"))]
Draft = Annotated[CaseAccess, Depends(require_case_access("request.draft"))]
Upload = Annotated[CaseAccess, Depends(require_case_access("case.upload"))]


class QuestionOut(BaseModel):
    id: uuid.UUID
    instruction_version_id: uuid.UUID | None
    number: int
    text_verbatim: str
    confirmed_text: str | None
    short_title: str = Field(
        description="A label of at most 40 characters for tabs and lists (the model's, else its first clause)."
    )
    source_word_ids: list[str]
    para_ref: str | None
    kind: str
    outside_expertise: bool
    outside_expertise_source: Literal["pack", "expert"] | None = Field(
        default=None, description="Who says the question is outside the expert's expertise."
    )
    answer_parts: list[str] | None = Field(
        default=None, description="Question-part keys still answered when the question is outside expertise."
    )
    status: str
    version: int


class AssumptionOut(BaseModel):
    id: uuid.UUID
    instruction_version_id: uuid.UUID | None
    number: int
    text_verbatim: str
    confirmed_text: str | None
    source_word_ids: list[str]
    para_ref: str | None
    status: str
    version: int


class LetterItem(BaseModel):
    """A cited paragraph of the letter, ready to show: the words the question or assumption came from."""

    id: uuid.UUID
    kind: Literal["question", "assumption"]
    para_ref: str
    text_verbatim: str
    outside_expertise: bool = False


class Questions(BaseModel):
    instruction_version_id: uuid.UUID | None
    version_no: int | None
    locked: bool
    reading: bool = Field(description="A letter is still being read.")
    read_failed: bool = Field(default=False, description="Reading a letter failed; it can be read again.")
    reopened: bool = Field(description="A new letter added a question to a confirmed set; confirm again.")
    rejected: int = Field(description="Suggestions refused because their words were not in the letter.")
    warnings: list[str] = Field(description="i18n keys.")
    questions: list[QuestionOut]
    assumptions: list[AssumptionOut]
    confirmed_count: int = Field(description="Questions and assumptions confirmed, of `total`.")
    total: int = Field(description="All questions and assumptions in the set, whatever `answerable` says.")
    letter: list[LetterItem] = Field(
        description="Items that cite a paragraph, in paragraph order (numbers count as numbers)."
    )


_PARA_PART = re.compile(r"(\d+)")


def _para_key(ref: str) -> list[tuple[int, int, str]]:
    """Paragraph references in reading order: digit runs compare as numbers, the rest as lower-case text."""
    return [(0, int(p), "") if p.isdigit() else (1, 0, p.lower()) for p in _PARA_PART.split(ref) if p]


_Q = (
    "select id, instruction_version_id, number, text_verbatim, confirmed_text, short_title, source_word_ids, para_ref,"
    " kind,"
    " outside_expertise, outside_expertise_source, answer_parts, status, version from app.questions"
)
_A = (
    "select id, instruction_version_id, number, text_verbatim, confirmed_text, source_word_ids, para_ref, status,"
    " version from app.assumptions"
)


def _question(row: dict[str, Any]) -> QuestionOut:
    """A stored question; one read before short titles existed gets its first clause."""
    return QuestionOut(**{**row, "short_title": short_title(row["text_verbatim"], row["short_title"])})


async def _latest(conn: DbConn, case_id: uuid.UUID) -> dict[str, Any] | None:
    """The latest version that can carry questions (oral instructions carry none), with how its reading went.

    `unread`: some letter with its file has not been read yet; `failed`: the case's latest reading job failed.
    """
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select v.id, v.version_no, v.locked_at is not null as locked, v.document_id,"
            " e.instruction_version_id is not null as read, coalesce(e.reopened, false) as reopened,"
            " coalesce(e.rejected, 0) as rejected,"
            " exists (select 1 from app.instruction_versions u where u.case_id = v.case_id and u.kind <> 'oral'"
            "   and u.document_id is not null and not exists (select 1 from app.instruction_extractions x"
            "   where x.instruction_version_id = u.id)) as unread,"
            " coalesce((select j.status = 'failed' from app.jobs j where j.case_id = v.case_id"
            "   and j.kind = 'extract_questions' order by j.created_at desc, j.updated_at desc limit 1), false)"
            "   as failed"
            " from app.instruction_versions v"
            " left join app.instruction_extractions e on e.instruction_version_id = v.id"
            " where v.case_id = %s and v.kind <> 'oral' order by v.version_no desc limit 1",
            (case_id,),
        )
        return await cur.fetchone()


@router.get("/cases/{case_id}/questions", response_model=Questions)
async def list_questions(
    case_id: uuid.UUID,
    access: ViewContent,
    conn: DbConn,
    answerable: Annotated[
        bool, Query(description="Only the questions the expert has confirmed or edited, in number order.")
    ] = False,
) -> Questions:
    latest = await _latest(conn, case_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{_Q} where case_id = %s order by number, id", (case_id,))  # noqa: S608
        questions = [_question(r) for r in await cur.fetchall()]
        await cur.execute(f"{_A} where case_id = %s order by number, id", (case_id,))  # noqa: S608
        assumptions = [AssumptionOut(**r) for r in await cur.fetchall()]
    cited = [(q.para_ref, q.id, "question", q.text_verbatim, q.outside_expertise) for q in questions if q.para_ref] + [
        (a.para_ref, a.id, "assumption", a.text_verbatim, False) for a in assumptions if a.para_ref
    ]
    letter = [
        LetterItem(id=i, kind=k, para_ref=ref, text_verbatim=text, outside_expertise=out)  # type: ignore[arg-type]
        for ref, i, k, text, out in sorted(cited, key=lambda c: _para_key(str(c[0])))
    ]
    confirmed_count = sum(1 for i in [*questions, *assumptions] if i.status == "confirmed")
    total = len(questions) + len(assumptions)
    locked = bool(latest and latest["locked"])
    unread = bool(latest and latest["unread"])
    read_failed = unread and bool(latest and latest["failed"])
    reading = unread and not read_failed
    reopened = bool(latest and latest["reopened"] and not locked)
    warnings = []
    if read_failed:
        warnings.append("setup.questions.warning.read_failed")
    if reopened:
        warnings.append("setup.questions.warning.reopened")
    if latest and latest["document_id"] is None and not locked:
        warnings.append("setup.questions.warning.letter_without_file")
    if latest and latest["rejected"]:
        warnings.append("setup.questions.warning.rejected")
    if any(q.outside_expertise for q in questions):
        warnings.append("setup.questions.warning.outside_expertise")
    return Questions(
        instruction_version_id=latest["id"] if latest else None,
        version_no=latest["version_no"] if latest else None,
        locked=locked,
        reading=reading,
        read_failed=read_failed,
        reopened=reopened,
        rejected=int(latest["rejected"]) if latest else 0,
        warnings=warnings,
        questions=[q for q in questions if q.status in ("confirmed", "edited")] if answerable else questions,
        assumptions=assumptions,
        confirmed_count=confirmed_count,
        total=total,
        letter=letter,
    )


class QuestionUpdate(BaseModel):
    """`text` is the expert's own wording (the exact words stay); `confirm` accepts the exact words."""

    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=3000)
    confirm: bool | None = None
    kind: str | None = Field(default=None, pattern=CODE)
    outside_expertise: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> QuestionUpdate:
        if self.model_dump(exclude_none=True) == {}:
            raise ValueError("nothing to change")
        if self.text is not None and self.confirm:
            raise ValueError("send your wording or confirm the exact words, not both")
        return self


class AssumptionUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str | None = Field(default=None, min_length=1, max_length=3000)
    confirm: bool | None = None

    @model_validator(mode="after")
    def _something(self) -> AssumptionUpdate:
        if self.text is None and not self.confirm:
            raise ValueError("nothing to change")
        if self.text is not None and self.confirm:
            raise ValueError("send your wording or confirm the exact words, not both")
        return self


async def _refuse_if_locked(conn: DbConn, case_id: uuid.UUID) -> None:
    latest = await _latest(conn, case_id)
    if latest and latest["locked"]:
        raise conflict("The questions are confirmed. A new letter reopens them.")


def _changes(status: str, text: str | None, confirm: bool | None) -> dict[str, Any]:
    if text is not None:
        if status == "confirmed":
            raise conflict("This question is already confirmed in its own words.")
        return {"confirmed_text": text, "status": "edited"}
    if confirm and status == "suggested":
        return {"status": "confirmed"}
    return {}


async def _update(conn: DbConn, table: str, row_id: uuid.UUID, version: int, changes: dict[str, Any]) -> None:
    if not changes:
        return
    sets = ", ".join(f"{k} = %s" for k in changes)
    try:
        cur = await conn.execute(
            f"update app.{table} set {sets} where id = %s and version = %s returning id",  # noqa: S608
            (*changes.values(), row_id, version),
        )
    except RaiseException:
        raise conflict("This change is not allowed.") from None
    if await cur.fetchone() is None:
        raise stale()


async def _row(conn: DbConn, sql: str, row_id: uuid.UUID, case_id: uuid.UUID) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{sql} where id = %s and case_id = %s", (row_id, case_id))  # noqa: S608
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    return row


@router.patch("/cases/{case_id}/questions/{question_id}", response_model=QuestionOut)
async def update_question(
    case_id: uuid.UUID,
    question_id: uuid.UUID,
    body: QuestionUpdate,
    access: Confirm,
    if_match: IfMatch,
    request: Request,
    response: Response,
    conn: DbConn,
) -> QuestionOut:
    row = await _row(conn, _Q, question_id, case_id)
    if row["version"] != if_match:
        raise stale()
    await _refuse_if_locked(conn, case_id)
    changes = _changes(row["status"], body.text, body.confirm)
    if body.kind is not None:
        pack = await _pack(request, conn, case_id)
        if body.kind not in {q["key"] for q in pack.question_types()}:
            raise invalid("kind")
        changes["kind"] = body.kind
    if body.outside_expertise is not None:
        changes["outside_expertise"] = body.outside_expertise
        if row["status"] == "suggested":  # a confirmed question keeps its parts and source (database guard)
            parts = (await _pack(request, conn, case_id)).outside_expertise_parts()
            on = body.outside_expertise
            changes["answer_parts"] = (parts or None) if on else None
            changes["outside_expertise_source"] = "expert" if on else None
    await _update(conn, "questions", question_id, if_match, changes)
    await audit.record(conn, action="question.changed", target=f"question:{question_id}", case_id=case_id)
    out = _question(await _row(conn, _Q, question_id, case_id))
    response.headers["etag"] = etag(out.version)
    return out


@router.patch("/cases/{case_id}/assumptions/{assumption_id}", response_model=AssumptionOut)
async def update_assumption(
    case_id: uuid.UUID,
    assumption_id: uuid.UUID,
    body: AssumptionUpdate,
    access: Confirm,
    if_match: IfMatch,
    response: Response,
    conn: DbConn,
) -> AssumptionOut:
    row = await _row(conn, _A, assumption_id, case_id)
    if row["version"] != if_match:
        raise stale()
    await _refuse_if_locked(conn, case_id)
    await _update(conn, "assumptions", assumption_id, if_match, _changes(row["status"], body.text, body.confirm))
    await audit.record(conn, action="assumption.changed", target=f"assumption:{assumption_id}", case_id=case_id)
    out = AssumptionOut(**await _row(conn, _A, assumption_id, case_id))
    response.headers["etag"] = etag(out.version)
    return out


class Confirmed(BaseModel):
    instruction_version_id: uuid.UUID
    locked: bool
    questions: int
    assumptions: int


@router.post("/cases/{case_id}/questions/confirm-all", response_model=Confirmed)
async def confirm_all(case_id: uuid.UUID, access: Confirm, conn: DbConn) -> Confirmed:
    latest = await _latest(conn, case_id)
    if latest is None:
        raise conflict("There are no instructions yet.")
    if latest["locked"]:
        raise conflict("The questions are already confirmed.")
    if latest["unread"]:
        if latest["failed"]:
            raise ApiError("letter_not_read", "The letter could not be read. Ask for it to be read again.", 409)
        raise conflict("The letter is still being read.")
    counts = []
    for table in ("questions", "assumptions"):
        await conn.execute(
            f"update app.{table} set status = 'confirmed' where case_id = %s and status = 'suggested'",  # noqa: S608
            (case_id,),
        )
        cur = await conn.execute(f"select count(*) from app.{table} where case_id = %s", (case_id,))  # noqa: S608
        found = await cur.fetchone()
        counts.append(int(found[0]) if found else 0)
    if counts[0] == 0:
        raise conflict("There are no questions to confirm.")
    await conn.execute("update app.instruction_versions set locked_at = now() where id = %s", (latest["id"],))
    await audit.record(conn, action="questions.locked", target=f"instruction_version:{latest['id']}", case_id=case_id)
    return Confirmed(instruction_version_id=latest["id"], locked=True, questions=counts[0], assumptions=counts[1])


class ReadAgain(BaseModel):
    job_id: uuid.UUID


@router.post("/cases/{case_id}/questions/read-again", status_code=202, response_model=ReadAgain)
async def read_again(case_id: uuid.UUID, access: Upload, conn: DbConn) -> ReadAgain:
    """Queue the reading again after it failed; 409 while a reading is under way or nothing is left to read."""
    latest = await _latest(conn, case_id)
    if latest is None or not latest["unread"]:
        raise conflict("Every letter has been read.")
    if not latest["failed"]:
        raise conflict("The letter is still being read.")
    job_id = await registry.enqueue(conn, case_id, "extract_questions")
    await audit.record(conn, action="questions.read_again", target=f"job:{job_id}", case_id=case_id)
    return ReadAgain(job_id=job_id)


class NoteRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic: NoteTopic
    question_ids: list[uuid.UUID] = Field(default_factory=list, max_length=60)


@router.post("/cases/{case_id}/instructing-party-notes/draft", response_model=NoteDraft)
async def draft_note(case_id: uuid.UUID, body: NoteRequest, access: Draft, conn: DbConn) -> NoteDraft:
    items: list[NoteItem] = []
    if body.question_ids:
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select id, number, para_ref, text_verbatim from app.questions where case_id = %s and id = any(%s)"
                " order by number",
                (case_id, body.question_ids),
            )
            rows = await cur.fetchall()
        if len(rows) != len(set(body.question_ids)):
            raise invalid("question_ids")
        items = [
            NoteItem(question_id=r["id"], number=r["number"], para_ref=r["para_ref"], text=r["text_verbatim"])
            for r in rows
        ]
    elif body.topic == "outside_expertise":
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                "select id, number, para_ref, text_verbatim from app.questions where case_id = %s"
                " and outside_expertise order by number",
                (case_id,),
            )
            items = [
                NoteItem(question_id=r["id"], number=r["number"], para_ref=r["para_ref"], text=r["text_verbatim"])
                for r in await cur.fetchall()
            ]
    return await draft(conn, case_id, body.topic, items)
