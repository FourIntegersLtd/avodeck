"""Each expert's own check settings (architecture 5.2 "13 Things to look at", slice D brief).

`GET /v1/me/checks` lists the person's settings; with `case_id` it lists the case's pack checks with
those settings applied (the person must be able to see the case, otherwise 404). Changes apply to the
next run only. Rules written by the expert (`POST /v1/me/checks/custom`) are not built yet (501).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from app.core.auth.access import check_case_access
from app.core.http import DbConn
from app.features.understand.common import case_pack, invalid
from app.features.understand.router import router

KEY = r"^[a-z0-9_]{1,60}$"


class Setting(BaseModel):
    check_key: str
    enabled: bool
    window_days: int | None


class Settings(BaseModel):
    settings: list[Setting]


class CaseCheck(BaseModel):
    key: str
    rule: str
    gentle: bool
    default_on: bool
    enabled: bool
    window_days: int | None


class CaseChecks(BaseModel):
    checks: list[CaseCheck]


class SettingChange(BaseModel):
    check_key: str = Field(pattern=KEY)
    enabled: bool | None = None
    window_days: int | None = Field(default=None, ge=1, le=3650)


async def _mine(conn: DbConn) -> dict[str, dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select check_key, enabled, window_days from app.check_config"
            " where person_id = app.current_person_id() order by check_key"
        )
        return {str(r["check_key"]): r for r in await cur.fetchall()}


@router.get("/me/checks", response_model=Settings | CaseChecks)
async def my_checks(request: Request, conn: DbConn, case_id: uuid.UUID | None = None) -> Settings | CaseChecks:
    mine = await _mine(conn)
    if case_id is None:
        return Settings(settings=[Setting(**r) for r in mine.values()])
    await check_case_access(conn, case_id, "case.view_content")
    pack = await case_pack(request, conn, case_id)
    out = []
    for check in pack.checks():
        key, own = str(check["key"]), mine.get(str(check["key"]), {})
        default_on = check.get("default_on", True) is not False
        window = own.get("window_days") or check.get("window_days")
        out.append(
            CaseCheck(
                key=key,
                rule=str(check["rule"]),
                gentle=bool(check.get("gentle", False)),
                default_on=default_on,
                enabled=own["enabled"] if "enabled" in own else default_on,
                window_days=int(window) if window is not None else None,
            )
        )
    return CaseChecks(checks=out)


@router.patch("/me/checks", response_model=Setting)
async def change_check(body: SettingChange, conn: DbConn) -> Setting:
    if body.enabled is None and body.window_days is None:
        raise invalid("enabled", "window_days")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "insert into app.check_config (person_id, check_key, enabled, window_days)"
            " values (app.current_person_id(), %s, coalesce(%s, true), %s)"
            " on conflict (person_id, check_key) do update set"
            " enabled = coalesce(%s, app.check_config.enabled),"
            " window_days = coalesce(%s, app.check_config.window_days), updated_at = now()"
            " returning check_key, enabled, window_days",
            (body.check_key, body.enabled, body.window_days, body.enabled, body.window_days),
        )
        row = await cur.fetchone()
    assert row is not None
    return Setting(**row)


@router.post("/me/checks/custom", status_code=501)
async def custom_check() -> JSONResponse:
    return JSONResponse(
        status_code=501, content={"error": {"code": "not_built", "message": "Your own checks are not ready yet."}}
    )
