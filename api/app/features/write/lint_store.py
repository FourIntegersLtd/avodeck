"""The opinion's warnings as stored (lint_issues): sealed words, open and dismissed (slice E, screen 15)."""

from __future__ import annotations

import uuid

from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core.contracts.opinions import BODY_PURPOSE
from app.core.http import DbConn
from app.features.write.ai_tasks import LINT_TYPES
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


class LintCheck(BaseModel):
    type: str
    passed: bool


# Checks only the model makes: when it was asked and could not answer, these have not been done.
MODEL_ONLY = ("outside_expertise",)
CHECKED = "opinion.model_checked"
UNCHECKED = "opinion.model_check_failed"


def checks_for(open_now: list[LintIssueOut], not_run: list[str] | None = None) -> list[LintCheck]:
    """Each kind of check the model and the pattern check make. A check passes when it ran and no warning of it
    is open; one that could not be run (`not_run`) has not passed."""
    skipped = not_run or []
    return [LintCheck(type=t, passed=t not in skipped and not any(i.type == t for i in open_now)) for t in LINT_TYPES]


async def checks_not_run(conn: DbConn, case_id: uuid.UUID, opinion_id: uuid.UUID) -> list[str]:
    """The checks the model was last asked to make on this saved text and could not (its answer was refused
    twice). A new save, or a later answer, clears it; a text the model was never asked about is not listed.
    Read from `opinion_lint_checks`, which everyone who may view the opinion can see (the audit trail is
    visible only to its own author)."""
    cur = await conn.execute(
        "select not c.model_answered from app.opinion_lint_checks c join app.opinions o on o.id = c.opinion_id"
        " where c.opinion_id = %s and o.case_id = %s and c.opinion_version = o.version",
        (opinion_id, case_id),
    )
    row = await cur.fetchone()
    return list(MODEL_ONLY) if row is not None and row[0] else []


async def record_model_check(conn: DbConn, opinion_id: uuid.UUID, version: int, *, answered: bool) -> None:
    """Remember, for everyone who may view the opinion, whether the model answered for this saved text."""
    await conn.execute(
        "insert into app.opinion_lint_checks (opinion_id, case_id, opinion_version, model_answered)"
        " select o.id, o.case_id, %s, %s from app.opinions o where o.id = %s"
        " on conflict (opinion_id) do update set opinion_version = excluded.opinion_version,"
        " model_answered = excluded.model_answered, checked_at = now()",
        (version, answered, opinion_id),
    )


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
