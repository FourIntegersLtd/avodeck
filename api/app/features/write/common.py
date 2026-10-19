"""Small helpers shared by the slice E routes: access, the case's packs, encryption and lookups."""

from __future__ import annotations

import re
import uuid
from datetime import date
from typing import Annotated

import psycopg
from fastapi import Depends, Request
from pydantic import BaseModel

from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts.facts import Fact, citable_facts
from app.core.crypto import CaseCrypto
from app.core.errors import ApiError
from app.core.http import DbConn
from app.core.packs import MergedPack, PackLibrary

Write = Annotated[CaseAccess, Depends(require_case_access("opinion.write"))]
View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]

# Questions an expert answers: confirmed as read, or confirmed with their own wording (slice C).
ANSWERABLE = ("confirmed", "edited")


def not_found() -> ApiError:
    return ApiError("not_found", "Not found.", 404)


def invalid(*fields: str) -> ApiError:
    return ApiError("invalid", "Check the highlighted fields.", 422, fields=list(fields))


def refused(exc: psycopg.Error) -> ApiError:
    """A change the database refuses (`app.refuse`, AV409) is 409; anything else is raised again."""
    if exc.sqlstate == "AV409":
        return ApiError("conflict", "This change is not allowed.", 409)
    raise exc


class PointFact(BaseModel):
    id: uuid.UUID
    page_id: uuid.UUID
    page_no: int
    kind: str
    value: str | None
    event_date: date | None


class QuestionHeader(BaseModel):
    """What the boards print about a question: its number, a short title, the kind (with the pack's label key)."""

    number: int
    short_title: str
    kind: str
    kind_label_key: str | None
    para_ref: str | None


_CLAUSE_END = re.compile(r"[,;:.?!](?:\s|$)|\s[-\u2013\u2014]\s")
SHORT_TITLE_MAX = 40


def short_title(text: str) -> str:
    """The first clause of a question, in at most 40 characters (cut at a word, ending "…"). Slice C may replace it."""
    first = _CLAUSE_END.split(" ".join(text.split()), maxsplit=1)[0].strip()
    if len(first) <= SHORT_TITLE_MAX:
        return first
    return first[: SHORT_TITLE_MAX - 1].rsplit(" ", 1)[0].rstrip() + "\u2026"


async def question_header(conn: DbConn, pack: MergedPack, case_id: uuid.UUID, question_id: uuid.UUID) -> QuestionHeader:
    cur = await conn.execute(
        "select number, kind, coalesce(confirmed_text, text_verbatim), para_ref from app.questions"
        " where id = %s and case_id = %s",
        (question_id, case_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    labels = {str(q["key"]): q.get("label_key") for q in pack.question_types()}
    return QuestionHeader(
        number=row[0], short_title=short_title(row[2]), kind=row[1], kind_label_key=labels.get(row[1]), para_ref=row[3]
    )


class Queued(BaseModel):
    job_id: uuid.UUID


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "Saving is not ready.", 503)
    return crypto


Crypto = Annotated[CaseCrypto, Depends(_crypto)]


async def case_pack(request: Request, conn: DbConn, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    library: PackLibrary = request.app.state.packs
    return library.combine(*row)


async def require_question(conn: DbConn, case_id: uuid.UUID, question_id: uuid.UUID) -> None:
    """404 unless the question is the case's and confirmed (an unconfirmed question has no opinion yet)."""
    cur = await conn.execute(
        "select 1 from app.questions where id = %s and case_id = %s and status = any(%s)",
        (question_id, case_id, list(ANSWERABLE)),
    )
    if await cur.fetchone() is None:
        raise not_found()


async def citable(request: Request, conn: DbConn, case_id: uuid.UUID) -> list[Fact]:
    """The facts an opinion may cite: slice B's read log decides which pages count (fail closed)."""
    return await citable_facts(conn, case_id, getattr(request.app.state, "page_reads", None))
