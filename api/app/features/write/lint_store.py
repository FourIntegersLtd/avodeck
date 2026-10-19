"""The opinion's warnings as stored (lint_issues): sealed words, open and dismissed (slice E, screen 15)."""

from __future__ import annotations

import uuid

from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core.contracts.opinions import BODY_PURPOSE
from app.core.http import DbConn
from app.features.write.common import Crypto
from app.features.write.lint_regex import sentence_spans

BODY = BODY_PURPOSE


def lint_purpose(opinion_id: uuid.UUID) -> str:
    """The purpose the words a warning quotes are sealed under."""
    return f"lint_words/{opinion_id}"


class LintIssueOut(BaseModel):
    id: uuid.UUID
    sentence_index: int
    sentence_start: int | None = None
    sentence_end: int | None = None
    type: str
    words: str
    source: str
    status: str


async def dismissed_issues(
    conn: DbConn, crypto: Crypto, case_id: uuid.UUID, opinion_id: uuid.UUID
) -> list[tuple[str, str]]:
    """(type, words) of the warnings the expert dismissed; the same warning is not raised again."""
    cur = await conn.execute(
        "select type, words from app.lint_issues where opinion_id = %s and status = 'dismissed'", (opinion_id,)
    )
    return [
        (r[0], (await crypto.decrypt(conn, case_id, bytes(r[1]), purpose=lint_purpose(opinion_id))).decode())
        for r in await cur.fetchall()
    ]


async def open_issues(
    conn: DbConn, crypto: Crypto, case_id: uuid.UUID, opinion_id: uuid.UUID, *, issue_id: uuid.UUID | None = None
) -> list[LintIssueOut]:
    """The opinion's open warnings, in the order of its sentences (or the one warning `issue_id`)."""
    where, arg = ("id = %s", issue_id) if issue_id else ("opinion_id = %s and status = 'open'", opinion_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, sentence_index, type, words, source, status from app.lint_issues"
            f" where {where} order by sentence_index, created_at, id",  # noqa: S608
            (arg,),
        )
        rows = await cur.fetchall()
    body_row = await (await conn.execute("select body from app.opinions where id = %s", (opinion_id,))).fetchone()
    spans = (
        sentence_spans((await crypto.decrypt(conn, case_id, bytes(body_row[0]), purpose=BODY)).decode())
        if body_row
        else []
    )
    return [
        LintIssueOut(
            **{
                **r,
                "sentence_start": spans[r["sentence_index"]][0] if r["sentence_index"] < len(spans) else None,
                "sentence_end": spans[r["sentence_index"]][1] if r["sentence_index"] < len(spans) else None,
                "words": (
                    await crypto.decrypt(conn, case_id, bytes(r["words"]), purpose=lint_purpose(opinion_id))
                ).decode(),
            }
        )
        for r in rows
    ]
