"""Screen 15: the expert's opinion on one question (architecture 5.2, slice E brief, scenario S9).

The text is the expert's own (rule 1), stored encrypted under the case key. Citations follow the
citation rule (`citations.py`). Saves take If-Match with the opinion's version (`"0"` before the
first save); a window that has fallen behind gets 412 `stale` carrying what it sent (`yours`) and
what is saved (`saved`), and the screen asks which to keep. The database keeps every save as a
version (migration 0502); restoring one saves it again as a new version. Every save runs the pattern
check again (`lint_regex`): the open warnings are replaced and returned with the opinion.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import Request, Response
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag
from app.core.packs import MergedPack
from app.features.write.citations import Citation, check_citations
from app.features.write.common import Crypto, View, Write, case_pack, citable, invalid, not_found, require_question
from app.features.write.lint_regex import lint
from app.features.write.lint_store import BODY, LintIssueOut, dismissed_issues, lint_purpose, open_issues
from app.features.write.quotes import same_words
from app.features.write.router import router


class OpinionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(max_length=100_000)
    citation_refs: list[Citation] = Field(default_factory=list, max_length=500)
    view: str | None = Field(default=None, max_length=40)
    provisional: bool = False


class OpinionOut(BaseModel):
    id: uuid.UUID
    question_id: uuid.UUID
    body: str
    citation_refs: list[Citation]
    view: str | None
    provisional: bool
    restored_from: int | None
    version: int
    updated_at: datetime
    lint: list[LintIssueOut] = Field(default_factory=list)


class OpinionVersion(BaseModel):
    version_no: int
    body: str
    citation_refs: list[Citation]
    view: str | None
    provisional: bool
    restored_from: int | None
    saved_by: uuid.UUID | None
    saved_at: datetime


class OpinionVersions(BaseModel):
    versions: list[OpinionVersion]


async def _decrypt(conn: DbConn, crypto: Crypto, case_id: uuid.UUID, blob: Any) -> str:
    return (await crypto.decrypt(conn, case_id, bytes(blob), purpose=BODY)).decode()


async def opinion_out(conn: DbConn, crypto: Crypto, row: dict[str, Any]) -> OpinionOut:
    return OpinionOut(**{**row, "body": await _decrypt(conn, crypto, row["case_id"], row["body"])})


async def _current(
    conn: DbConn, case_id: uuid.UUID, *, question_id: uuid.UUID | None = None, opinion_id: uuid.UUID | None = None
) -> dict[str, Any] | None:
    column, value = ("question_id", question_id) if opinion_id is None else ("id", opinion_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select * from app.opinions where case_id = %s and {column} = %s for update",  # noqa: S608
            (case_id, value),
        )
        return await cur.fetchone()


async def _check(request: Request, conn: DbConn, case_id: uuid.UUID, saving: OpinionIn) -> MergedPack:
    pack = await case_pack(request, conn, case_id)
    if saving.view is not None and saving.view not in {v["code"] for v in pack.opinion_views()}:
        raise invalid("view")
    check_citations(saving.body, saving.citation_refs, await citable(request, conn, case_id))
    return pack


async def _relint(conn: DbConn, crypto: Crypto, row: dict[str, Any], body: str, pack: MergedPack) -> OpinionOut:
    """Replace the open warnings with the pattern check's on the saved text; the opinion with them."""
    case_id, opinion_id = row["case_id"], row["id"]
    await conn.execute("delete from app.lint_issues where opinion_id = %s and status = 'open'", (opinion_id,))
    dismissed = await dismissed_issues(conn, crypto, case_id, opinion_id)
    for issue in lint(body, pack.loaded_language()):
        if any(t == issue.type and same_words(w, issue.words) for t, w in dismissed):
            continue
        sealed = await crypto.encrypt(conn, case_id, issue.words.encode(), purpose=lint_purpose(opinion_id))
        await conn.execute(
            "insert into app.lint_issues (case_id, opinion_id, opinion_version, sentence_index, type, words, source)"
            " values (%s, %s, %s, %s, %s, %s, 'regex')",
            (case_id, opinion_id, row["version"], issue.sentence_index, issue.type, sealed),
        )
    out = await opinion_out(conn, crypto, row)
    out.lint = await open_issues(conn, crypto, case_id, opinion_id)
    return out


async def _write(
    conn: DbConn,
    crypto: Crypto,
    case_id: uuid.UUID,
    question_id: uuid.UUID,
    current: dict[str, Any] | None,
    saving: OpinionIn,
    restored_from: int | None = None,
) -> dict[str, Any]:
    values = {
        "body": await crypto.encrypt(conn, case_id, saving.body.encode(), purpose=BODY),
        "refs": Jsonb([c.model_dump(mode="json") for c in saving.citation_refs]),
        "view": saving.view,
        "provisional": saving.provisional,
        "restored_from": restored_from,
    }
    async with conn.cursor(row_factory=dict_row) as cur:
        if current is None:
            await cur.execute(
                "insert into app.opinions (case_id, question_id, body, citation_refs, view, provisional, restored_from)"
                " values (%(case)s, %(question)s, %(body)s, %(refs)s, %(view)s, %(provisional)s, %(restored_from)s)"
                " returning *",
                {**values, "case": case_id, "question": question_id},
            )
        else:
            await cur.execute(
                "update app.opinions set body = %(body)s, citation_refs = %(refs)s, view = %(view)s,"
                " provisional = %(provisional)s, restored_from = %(restored_from)s where id = %(id)s returning *",
                {**values, "id": current["id"]},
            )
        row = await cur.fetchone()
    assert row is not None
    return row


async def _stale(conn: DbConn, crypto: Crypto, current: dict[str, Any] | None, yours: OpinionIn) -> ApiError:
    saved = None if current is None else (await opinion_out(conn, crypto, current)).model_dump(mode="json")
    return ApiError(
        "stale",
        "Someone else changed this first. Choose which version to keep.",
        412,
        yours=yours.model_dump(mode="json"),
        saved=saved,
    )


@router.put("/cases/{case_id}/questions/{question_id}/opinion", response_model=OpinionOut)
async def save_opinion(
    case_id: uuid.UUID,
    question_id: uuid.UUID,
    saving: OpinionIn,
    if_match: IfMatch,
    request: Request,
    response: Response,
    access: Write,
    conn: DbConn,
    crypto: Crypto,
) -> OpinionOut:
    """Autosave: the expert's text, citations, view and provisional flag. Every save is kept as a version."""
    await require_question(conn, case_id, question_id)
    current = await _current(conn, case_id, question_id=question_id)
    if (0 if current is None else current["version"]) != if_match:
        raise await _stale(conn, crypto, current, saving)
    pack = await _check(request, conn, case_id, saving)
    try:
        async with conn.transaction():
            row = await _write(conn, crypto, case_id, question_id, current, saving)
    except UniqueViolation:
        raise await _stale(conn, crypto, await _current(conn, case_id, question_id=question_id), saving) from None
    response.headers["ETag"] = etag(row["version"])
    return await _relint(conn, crypto, row, saving.body, pack)


@router.get("/cases/{case_id}/opinions/{opinion_id}/versions", response_model=OpinionVersions)
async def opinion_versions(
    case_id: uuid.UUID,
    opinion_id: uuid.UUID,
    access: View,
    conn: DbConn,
    crypto: Crypto,
) -> OpinionVersions:
    """Every saved version of the opinion, newest first."""
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from app.opinion_versions where case_id = %s and opinion_id = %s order by version_no desc",
            (case_id, opinion_id),
        )
        rows = await cur.fetchall()
    if not rows:
        raise not_found()
    return OpinionVersions(
        versions=[
            OpinionVersion(
                **{
                    k: r[k] for k in ("version_no", "citation_refs", "view", "provisional", "restored_from", "saved_by")
                },
                body=await _decrypt(conn, crypto, case_id, r["body"]),
                saved_at=r["created_at"],
            )
            for r in rows
        ]
    )


@router.post("/cases/{case_id}/opinions/{opinion_id}/versions/{version_no}/restore", response_model=OpinionOut)
async def restore_version(
    case_id: uuid.UUID,
    opinion_id: uuid.UUID,
    version_no: int,
    if_match: IfMatch,
    request: Request,
    response: Response,
    access: Write,
    conn: DbConn,
    crypto: Crypto,
) -> OpinionOut:
    """Save an earlier version again as the newest one; the earlier version is not changed."""
    current = await _current(conn, case_id, opinion_id=opinion_id)
    if current is None:
        raise not_found()
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select * from app.opinion_versions where opinion_id = %s and version_no = %s", (opinion_id, version_no)
        )
        old = await cur.fetchone()
    if old is None:
        raise not_found()
    restoring = OpinionIn(
        body=await _decrypt(conn, crypto, case_id, old["body"]),
        citation_refs=old["citation_refs"],
        view=old["view"],
        provisional=old["provisional"],
    )
    if current["version"] != if_match:
        raise await _stale(conn, crypto, current, restoring)
    pack = await _check(request, conn, case_id, restoring)
    row = await _write(conn, crypto, case_id, current["question_id"], current, restoring, restored_from=version_no)
    response.headers["ETag"] = etag(row["version"])
    return await _relint(conn, crypto, row, restoring.body, pack)
