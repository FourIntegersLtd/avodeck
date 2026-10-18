"""Screen 17, the colleague's side: open a spot-check link and answer it, signed out (threat model T9).

The colleague is not a person in Avodeck, so there is no session and no `require_case_access`: the link
token and the emailed code, both kept only as hashes, are the gate, and migration 0604's functions check
them, count wrong codes, lock the link after five and audit every attempt with no person.

- `GET /v1/spot-checks/{token}` with the code in `X-Spot-Check-Code`: the cited values and crops.
- `POST /v1/spot-checks/{token}/responses` with the code and the answers: kept once.

Each address may try spot-check links `ADDRESS_LIMIT` times in `ADDRESS_WINDOW`, across every link, before
it gets 429 `too_many_attempts` (migration 0607; the address is kept only as a hash). Behind the platform's
proxy the address is the one uvicorn takes from the forwarded headers.

Every outcome is a 200 with a `status` (`ok`, `bad_code`, `locked`, `expired`, `unknown`; answers also
`already` and `invalid`). A wrong code must never raise: the transaction commits so the attempt counts.
Built on foundation's public router and mounted by `app/main.py` (the CSRF guard names the POST).
"""

from __future__ import annotations

import base64
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Header, Path, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.db import Database
from app.core.errors import ApiError
from app.core.http import public_router
from app.features.report.common import Storage
from app.features.report.spot_checks import open_check, respond

colleague_router = public_router(prefix="/v1", tags=["spot-checks"])

Token = Annotated[str, Path(min_length=1, max_length=128)]
Code = Annotated[str, Field(pattern=r"^\d{6}$")]
_REASON = r"^[a-z][a-z0-9_]{0,39}$"
ADDRESS_LIMIT = 20
ADDRESS_WINDOW = timedelta(minutes=15)
_TOO_MANY: dict[int | str, dict[str, Any]] = {429: {"description": "Too many tries from this address; wait 15 minutes"}}


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


async def _count_try(request: Request) -> None:
    """One try from this address, counted in its own transaction so a refusal still counts."""
    address = request.client.host if request.client else ""
    hashed = hashlib.sha256(f"spot-check-address:{address}".encode()).hexdigest()
    async with _database(request).anonymous() as conn:
        cur = await conn.execute(
            "select app.spot_check_address_try(%s, %s, %s)", (hashed, ADDRESS_LIMIT, ADDRESS_WINDOW)
        )
        row = await cur.fetchone()
    if row is None or not row[0]:
        raise ApiError("too_many_attempts", "Too many tries. Wait 15 minutes and try again.", 429)


@colleague_router.get("/spot-checks/{token}", response_model=ColleagueView, responses=_TOO_MANY)
async def open_spot_check(
    token: Token,
    request: Request,
    response: Response,
    storage: Storage,
    code: Annotated[str, Header(alias="X-Spot-Check-Code", pattern=r"^\d{6}$")],
) -> ColleagueView:
    response.headers["Cache-Control"] = "private, no-store"
    await _count_try(request)
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


@colleague_router.post("/spot-checks/{token}/responses", response_model=ColleagueReplyOut, responses=_TOO_MANY)
async def answer_spot_check(token: Token, body: ColleagueReply, request: Request) -> ColleagueReplyOut:
    await _count_try(request)
    answers = [a.model_dump(mode="json", exclude_none=True) for a in body.answers]
    async with _database(request).anonymous() as conn:  # commits a wrong code's count
        status = await respond(conn, token, body.code, answers)
    return ColleagueReplyOut(status=status)  # type: ignore[arg-type]
