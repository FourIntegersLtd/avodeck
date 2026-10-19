"""Each expert's own check settings (architecture 5.2 "13 Things to look at", slice D brief).

`GET /v1/me/checks` lists the person's settings; with `case_id` it lists the case's pack checks with
those settings applied (the person must be able to see the case, otherwise 404). Changes apply to the
next run only. An expert may add a check of their own (`POST /v1/me/checks/custom`): a label and the settings of
one rule
type the engine already has ("nothing followed this entry within N days"), using only kinds the case's pack has.
"""

from __future__ import annotations

import secrets
import uuid
from typing import Any

from fastapi import Request
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
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
    label: str | None = None
    custom: bool = False


class CaseChecks(BaseModel):
    checks: list[CaseCheck]


class SettingChange(BaseModel):
    check_key: str = Field(pattern=KEY)
    enabled: bool | None = None
    window_days: int | None = Field(default=None, ge=1, le=3650)


async def _mine(conn: DbConn) -> dict[str, dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select check_key, enabled, window_days, label, custom from app.check_config"
            " where person_id = app.current_person_id() order by check_key"
        )
        return {str(r["check_key"]): r for r in await cur.fetchall()}


def _own_check(row: dict[str, Any]) -> CaseCheck:
    own = row["custom"]
    return CaseCheck(
        key=str(row["check_key"]),
        rule=str(own["rule"]),
        gentle=False,
        default_on=True,
        enabled=row["enabled"],
        window_days=row["window_days"] if row["window_days"] is not None else own.get("window_days"),
        label=row["label"],
        custom=True,
    )


@router.get("/me/checks", response_model=Settings | CaseChecks)
async def my_checks(request: Request, conn: DbConn, case_id: uuid.UUID | None = None) -> Settings | CaseChecks:
    mine = await _mine(conn)
    if case_id is None:
        return Settings(settings=[Setting(**{k: r[k] for k in Setting.model_fields}) for r in mine.values()])
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
    out += [_own_check(r) for r in mine.values() if r["custom"]]
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


class OwnCheck(BaseModel):
    case_id: uuid.UUID
    label: str = Field(min_length=1, max_length=80)
    applies_to: str = Field(pattern=KEY)
    followup_kinds: list[str] = Field(min_length=1, max_length=20)
    window_days: int = Field(ge=1, le=3650)


@router.post("/me/checks/custom", status_code=201, response_model=CaseCheck)
async def custom_check(body: OwnCheck, request: Request, conn: DbConn) -> CaseCheck:
    """Add a check of your own for cases of this kind: one rule type (`not_followed_up`), kinds from the pack."""
    await check_case_access(conn, body.case_id, "case.view_content")
    pack = await case_pack(request, conn, body.case_id)
    known = {str(k["key"]) for k in pack.fact_kinds()}
    label = body.label.strip()
    if not label:
        raise invalid("label")
    if body.applies_to not in known:
        raise invalid("applies_to")
    if not set(body.followup_kinds) <= known:
        raise invalid("followup_kinds")
    own = {
        "rule": "not_followed_up",
        "applies_to": body.applies_to,
        "followup_kinds": sorted(set(body.followup_kinds)),
        "window_days": body.window_days,
    }
    key = f"own_{secrets.token_hex(4)}"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "insert into app.check_config (person_id, check_key, enabled, window_days, label, custom)"
            " values (app.current_person_id(), %s, true, %s, %s, %s)"
            " returning check_key, enabled, window_days, label, custom",
            (key, body.window_days, label, Jsonb(own)),
        )
        row = await cur.fetchone()
    assert row is not None
    return _own_check(row)
