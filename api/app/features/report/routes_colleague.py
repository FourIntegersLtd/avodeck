"""Screen 17, the colleague's side: open a spot-check link and answer it, signed out (threat model T9).

The colleague is not a person in Avodeck, so there is no session and no `require_case_access`: the link
token and the emailed code, both kept only as hashes, are the gate, and migration 0604's functions check
them, count wrong codes, lock the link after five and audit every attempt with no person.

- `GET /v1/spot-checks/{token}` with the code in `X-Spot-Check-Code`: the cited values and crops.
- `POST /v1/spot-checks/{token}/responses` with the code and the answers: kept once.

Every outcome is a 200 with a `status` (`ok`, `bad_code`, `locked`, `expired`, `unknown`; answers also
`already` and `invalid`). A wrong code must never raise: the transaction commits so the attempt counts.
Built on foundation's public router and mounted by `app/main.py` (the CSRF guard names the POST).
"""

from __future__ import annotations

import base64
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Header, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.db import Database
from app.core.http import public_router
from app.features.report.common import Storage
from app.features.report.spot_checks import open_check, respond

colleague_router = public_router(prefix="/v1", tags=["spot-checks"])

Token = Annotated[str, Path(min_length=1, max_length=128)]
Code = Annotated[str, Field(pattern=r"^\d{6}$")]
_REASON = r"^[a-z][a-z0-9_]{0,39}$"


class ColleagueItem(BaseModel):
    fact_id: uuid.UUID
    value: str
    crop: str | None = Field(description="A PNG of the area around the cited words, base64")


class ColleagueView(BaseModel):
    status: Literal["ok", "bad_code", "locked", "expired", "unknown"]
    expires_at: datetime | None = None
    responded: bool = False
    items: list[ColleagueItem] = []


class ColleagueAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    fact_id: uuid.UUID
    agrees: bool = Field(strict=True)
    reason: str | None = Field(default=None, pattern=_REASON)


class ColleagueReply(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: Code
    answers: list[ColleagueAnswer] = Field(min_length=1, max_length=50)


class ColleagueReplyOut(BaseModel):
    status: Literal["ok", "already", "invalid", "bad_code", "locked", "expired", "unknown"]


def _database(request: Request) -> Database:
    database: Database = request.app.state.database
    return database


@colleague_router.get("/spot-checks/{token}", response_model=ColleagueView)
async def open_spot_check(
    token: Token,
    request: Request,
    response: Response,
    storage: Storage,
    code: Annotated[str, Header(alias="X-Spot-Check-Code", pattern=r"^\d{6}$")],
) -> ColleagueView:
    response.headers["Cache-Control"] = "private, no-store"
    async with _database(request).anonymous() as conn:  # commits a wrong code's count
        opened = await open_check(conn, storage, token, code)
    if opened.outcome != "ok" or opened.items is None:
        return ColleagueView(status=opened.outcome)  # type: ignore[arg-type]
    return ColleagueView(
        status="ok",
        expires_at=opened.expires_at,
        responded=opened.responded,
        items=[
            ColleagueItem(fact_id=i.fact_id, value=i.value, crop=base64.b64encode(i.crop).decode() if i.crop else None)
            for i in opened.items
        ],
    )


@colleague_router.post("/spot-checks/{token}/responses", response_model=ColleagueReplyOut)
async def answer_spot_check(token: Token, body: ColleagueReply, request: Request) -> ColleagueReplyOut:
    answers = [a.model_dump(mode="json", exclude_none=True) for a in body.answers]
    async with _database(request).anonymous() as conn:  # commits a wrong code's count
        status = await respond(conn, token, body.code, answers)
    return ColleagueReplyOut(status=status)  # type: ignore[arg-type]
