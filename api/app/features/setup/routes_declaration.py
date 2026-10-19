"""Screen 02: the expert's declaration. The wording is the jurisdiction pack's and is read-only; the expert
may add words to each declaration until their first served report (then 409)."""

from __future__ import annotations

import re
from typing import Annotated, Any

from fastapi import Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.features.setup.common import cell, invalid, library, not_found
from app.features.setup.router import router

PACK_CODE = r"^[a-z][a-z0-9-]{0,39}$"


class Wording(BaseModel):
    key: str
    rule_ref: str | None = None
    version: str | None = None
    text: str
    points: int | None = Field(
        default=None, description="How many numbered points the wording has; null if it has none."
    )


class Addition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$", description="The key of the pack declaration it adds to")
    text: str = Field(min_length=1, max_length=4000)


class Declaration(BaseModel):
    jurisdiction: str
    wording: list[Wording]
    additions: list[Addition]
    locked: bool = Field(description="True once the expert has served a report: nothing can change")


class DeclarationUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jurisdiction: str = Field(pattern=PACK_CODE)
    additions: list[Addition] = Field(max_length=20)


async def _jurisdiction(request: Request, conn: DbConn, code: str | None) -> str:
    if code is None:
        cur = await conn.execute(
            "select default_jurisdiction from app.experts where person_id = app.current_person_id()"
        )
        row = await cur.fetchone()
        code = row[0] if row else None
    if code is None or code not in {p.code for p in library(request).jurisdictions_in(cell(request))}:
        raise not_found()
    return code


_POINT = re.compile(r"^\s*\d{1,3}[.)]\s")


def _points(text: str) -> int | None:
    return sum(1 for line in text.splitlines() if _POINT.match(line)) or None


def _wording(request: Request, code: str) -> list[dict[str, Any]]:
    wording: list[dict[str, Any]] = library(request).jurisdiction_summary(code)["declarations"]
    return wording


async def _declaration(request: Request, conn: DbConn, code: str, response: Response) -> Declaration:
    cur = await conn.execute(
        "select kind, text from app.declarations"
        " where expert_id = app.current_person_id() and jurisdiction = %s and text is not null order by kind",
        (code,),
    )
    additions = [Addition(kind=k, text=t) for k, t in await cur.fetchall()]
    cur = await conn.execute(
        "select coalesce(sum(version), 0), app.expert_has_served(app.current_person_id()) from app.declarations"
        " where expert_id = app.current_person_id() and jurisdiction = %s",
        (code,),
    )
    row = await cur.fetchone()
    assert row is not None
    response.headers["etag"] = etag(int(row[0]))
    wording = [Wording(**w, points=_points(w["text"])) for w in _wording(request, code)]
    return Declaration(jurisdiction=code, wording=wording, additions=additions, locked=bool(row[1]))


def _tag(response: Response) -> int:
    return int(response.headers["etag"].removeprefix("W/").strip('"'))


@router.get("/me/declaration", response_model=Declaration)
async def get_declaration(
    request: Request,
    conn: DbConn,
    response: Response,
    jurisdiction: Annotated[str | None, Query(pattern=PACK_CODE)] = None,
) -> Declaration:
    return await _declaration(request, conn, await _jurisdiction(request, conn, jurisdiction), response)


@router.put("/me/declaration", response_model=Declaration)
async def put_declaration(
    body: DeclarationUpdate, if_match: IfMatch, request: Request, conn: DbConn, response: Response
) -> Declaration:
    code = await _jurisdiction(request, conn, body.jurisdiction)
    current = await _declaration(request, conn, code, response)
    if current.locked:
        raise ApiError("conflict", "Your declaration is fixed after your first served report.", 409)
    if _tag(response) != if_match:
        raise stale()
    keys = [w.key for w in current.wording]
    wanted = {a.kind: a.text for a in body.additions}
    if len(wanted) != len(body.additions) or any(k not in keys for k in wanted):
        raise invalid("additions")
    for kind in sorted(set(wanted) | {a.kind for a in current.additions}):
        await conn.execute(
            "insert into app.declarations (expert_id, jurisdiction, kind, text)"
            " values (app.current_person_id(), %(j)s, %(k)s, %(t)s)"
            " on conflict (expert_id, jurisdiction, kind) do update set text = excluded.text"
            " where app.declarations.text is distinct from excluded.text",
            {"j": code, "k": kind, "t": wanted.get(kind)},
        )
    return await _declaration(request, conn, code, response)
