"""Screen 15: the model's warnings on the opinion (T-15a), and dismissing a warning (slice E brief).

`POST .../opinion/lint` answers in real time (architecture 5.2, under 2 s): it sends the saved opinion,
sentence by sentence, with its citation quotes, through the AI kit (identifiers out first, rule 7; the
specialty's outside-expertise question types in the context). A warning is kept only when its words are
in the sentence it names, and is stored with the sentence's own words; one the expert dismissed before,
or already open, is not added again. Two wrong answers leave the pattern check's warnings as they are.
The named expert dismisses a warning with a reason (sealed under the case key); the dismissal is kept.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg
from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.ai.kit import InvalidOutput
from app.core.http import DbConn
from app.features.write import ai_tasks, services
from app.features.write.common import Crypto, View, Write, case_pack, not_found, refused, require_question
from app.features.write.lint_regex import sentences
from app.features.write.lint_store import (
    BODY,
    LintCheck,
    LintIssueOut,
    checks_for,
    dismissed_issues,
    lint_purpose,
    open_issues,
)
from app.features.write.quotes import locate, same_words
from app.features.write.router import router

logger = logging.getLogger("app.write")


class LintOut(BaseModel):
    issues: list[LintIssueOut]
    open_issues: list[LintIssueOut]
    checks: list[LintCheck]


async def _state(conn: DbConn, crypto: Crypto, case_id: uuid.UUID, opinion_id: uuid.UUID) -> LintOut:
    now = await open_issues(conn, crypto, case_id, opinion_id)
    return LintOut(issues=now, open_issues=now, checks=checks_for(now))


class LintDismiss(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dismissed_reason: str = Field(min_length=1, max_length=200)


def _reason_purpose(issue_id: uuid.UUID) -> str:
    return f"lint_reason/{issue_id}"


@router.post("/cases/{case_id}/questions/{question_id}/opinion/lint", response_model=LintOut)
async def lint_opinion(
    case_id: uuid.UUID, question_id: uuid.UUID, request: Request, access: Write, conn: DbConn, crypto: Crypto
) -> LintOut:
    """Ask the model which words in the saved opinion to check; the warnings open after it answers."""
    await require_question(conn, case_id, question_id)
    cur = await conn.execute(
        "select id, body, citation_refs, version from app.opinions where case_id = %s and question_id = %s",
        (case_id, question_id),
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    opinion_id, sealed, refs, version = row
    body = (await crypto.decrypt(conn, case_id, bytes(sealed), purpose=BODY)).decode()
    said = sentences(body)
    if said:
        await _ask(request, conn, crypto, case_id, opinion_id, version, said, refs)
    return await _state(conn, crypto, case_id, opinion_id)


@router.get("/cases/{case_id}/questions/{question_id}/opinion/lint", response_model=LintOut)
async def lint_state(case_id: uuid.UUID, question_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto) -> LintOut:
    """The opinion's open warnings and which checks passed, without asking the model again."""
    await require_question(conn, case_id, question_id)
    cur = await conn.execute(
        "select id from app.opinions where case_id = %s and question_id = %s", (case_id, question_id)
    )
    row = await cur.fetchone()
    if row is None:
        return LintOut(issues=[], open_issues=[], checks=checks_for([]))
    return await _state(conn, crypto, case_id, row[0])


async def _ask(
    request: Request,
    conn: DbConn,
    crypto: Crypto,
    case_id: uuid.UUID,
    opinion_id: uuid.UUID,
    version: int,
    said: list[str],
    refs: list[dict[str, Any]],
) -> None:
    pack = await case_pack(request, conn, case_id)
    lines = [f"sentence\t{i}\t{' '.join(s.split())}" for i, s in enumerate(said)]
    lines += [f"citation\t{n}\t{' '.join(str(c['quote_verbatim']).split())}" for n, c in enumerate(refs, 1)]
    task = ai_tasks.t15a(pack, len(said))
    current = services.current()
    names = await current.party_names().case_names(conn, case_id)
    births = await current.birth_dates().dates_of_birth(conn, case_id)
    try:
        out: dict[str, Any] = await current.ai().run(
            conn,
            task,
            pack=pack,
            text="\n".join(lines),
            case_id=case_id,
            names=names,
            dates_of_birth=births,
            subject=f"opinion:{opinion_id}",
        )
    except InvalidOutput:
        logger.warning("T-15a answer refused twice for one opinion; pattern check warnings only")
        return
    known = [(i.sentence_index, i.type, i.words) for i in await open_issues(conn, crypto, case_id, opinion_id)]
    dismissed = await dismissed_issues(conn, crypto, case_id, opinion_id)
    for item in out["issues"]:
        index, kind = item["sentence_index"], item["type"]
        found = locate(item["words"], [(str(n), w) for n, w in enumerate(said[index].split())])
        if found is None:
            continue
        words = " ".join(w for _, w in found)
        if any(i == index and t == kind and same_words(w, words) for i, t, w in known):
            continue
        if any(t == kind and same_words(w, words) for t, w in dismissed):
            continue
        known.append((index, kind, words))
        await conn.execute(
            "insert into app.lint_issues (case_id, opinion_id, opinion_version, sentence_index, type, words, source)"
            " values (%s, %s, %s, %s, %s, %s, 'ai')",
            (
                case_id,
                opinion_id,
                version,
                index,
                kind,
                await crypto.encrypt(conn, case_id, words.encode(), purpose=lint_purpose(opinion_id)),
            ),
        )


@router.patch("/cases/{case_id}/lint-issues/{issue_id}", response_model=LintIssueOut)
async def dismiss_issue(
    case_id: uuid.UUID, issue_id: uuid.UUID, dismissing: LintDismiss, access: Write, conn: DbConn, crypto: Crypto
) -> LintIssueOut:
    """Dismiss a warning with the expert's reason; the dismissal is kept for the final check."""
    cur = await conn.execute(
        "select opinion_id from app.lint_issues where case_id = %s and id = %s for update", (case_id, issue_id)
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    reason = await crypto.encrypt(
        conn, case_id, dismissing.dismissed_reason.encode(), purpose=_reason_purpose(issue_id)
    )
    try:
        async with conn.transaction():
            await conn.execute(
                "update app.lint_issues set status = 'dismissed', dismissed_reason = %s where id = %s",
                (reason, issue_id),
            )
    except psycopg.Error as exc:
        raise refused(exc) from None
    await audit.record(conn, action="lint_issue.dismissed", target=f"lint_issue:{issue_id}", case_id=case_id)
    [out] = await open_issues(conn, crypto, case_id, row[0], issue_id=issue_id)
    return out
