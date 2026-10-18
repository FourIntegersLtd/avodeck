"""Comments on a page, fact or paragraph (architecture 5.4, slice D brief).

Any reader who may comment (`comment.write`) adds one; only the author changes the words; anyone
who may comment resolves it. Comments are working notes and never go into a report.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Depends, Response
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.http import DbConn, IfMatch, etag, stale
from app.features.understand.common import forbidden, invalid, not_found
from app.features.understand.router import router

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Write = Annotated[CaseAccess, Depends(require_case_access("comment.write"))]

TargetKind = Literal["page", "fact", "paragraph"]


class CommentIn(BaseModel):
    target_kind: TargetKind
    target_id: str = Field(min_length=1, max_length=80)
    body: str = Field(min_length=1, max_length=4000)


class CommentChange(BaseModel):
    body: str | None = Field(default=None, min_length=1, max_length=4000)
    resolved: bool | None = None


class CommentOut(BaseModel):
    id: uuid.UUID
    target_kind: str
    target_id: str
    body: str
    author_id: uuid.UUID
    mine: bool
    resolved_at: datetime | None
    created_at: datetime
    version: int


class Comments(BaseModel):
    comments: list[CommentOut]


_C = (
    "select id, target_kind, target_id, body, author_id, author_id = app.current_person_id() as mine,"
    " resolved_at, created_at, version from app.comments"
)


async def _one(conn: DbConn, case_id: uuid.UUID, comment_id: uuid.UUID) -> CommentOut:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{_C} where id = %s and case_id = %s", (comment_id, case_id))
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    return CommentOut(**row)


@router.get("/cases/{case_id}/comments", response_model=Comments)
async def list_comments(
    case_id: uuid.UUID,
    access: View,
    conn: DbConn,
    target_kind: TargetKind | None = None,
    target_id: str | None = None,
) -> Comments:
    sql, args = f"{_C} where case_id = %s", list[object]([case_id])
    if target_kind is not None:
        sql, args = f"{sql} and target_kind = %s", [*args, target_kind]
    if target_id is not None:
        sql, args = f"{sql} and target_id = %s", [*args, target_id]
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{sql} order by created_at, id", args)
        rows = await cur.fetchall()
    return Comments(comments=[CommentOut(**r) for r in rows])


@router.post("/cases/{case_id}/comments", status_code=201, response_model=CommentOut)
async def add_comment(
    case_id: uuid.UUID, body: CommentIn, access: Write, response: Response, conn: DbConn
) -> CommentOut:
    if not body.body.strip():
        raise invalid("body")
    cur = await conn.execute(
        "insert into app.comments (case_id, target_kind, target_id, body, author_id)"
        " values (%s, %s, %s, %s, app.current_person_id()) returning id",
        (case_id, body.target_kind, body.target_id, body.body),
    )
    row = await cur.fetchone()
    assert row is not None
    await audit.record(conn, action="comment.added", target=f"comment:{row[0]}", case_id=case_id)
    out = await _one(conn, case_id, row[0])
    response.headers["etag"] = etag(out.version)
    return out


@router.patch("/cases/{case_id}/comments/{comment_id}", response_model=CommentOut)
async def change_comment(
    case_id: uuid.UUID,
    comment_id: uuid.UUID,
    body: CommentChange,
    access: Write,
    if_match: IfMatch,
    response: Response,
    conn: DbConn,
) -> CommentOut:
    current = await _one(conn, case_id, comment_id)
    if current.version != if_match:
        raise stale()
    if body.body is not None and not current.mine:
        raise forbidden()
    if body.body is not None and not body.body.strip():
        raise invalid("body")
    sets, args = ["version = version + 1", "updated_at = now()"], []
    if body.body is not None:
        sets.append("body = %s")
        args.append(body.body)
    if body.resolved is True:
        sets.append("resolved_at = coalesce(resolved_at, now())")
        sets.append("resolved_by = coalesce(resolved_by, app.current_person_id())")
    elif body.resolved is False:
        sets.append("resolved_at = null, resolved_by = null")
    cur = await conn.execute(
        f"update app.comments set {', '.join(sets)} where id = %s and version = %s",  # noqa: S608
        (*args, comment_id, if_match),
    )
    if cur.rowcount != 1:
        raise stale()
    action = "comment.resolved" if body.resolved and body.body is None else "comment.changed"
    await audit.record(conn, action=action, target=f"comment:{comment_id}", case_id=case_id)
    out = await _one(conn, case_id, comment_id)
    response.headers["etag"] = etag(out.version)
    return out
