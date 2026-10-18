"""Screen 18b: time spent on the case, counted quietly for the invoice.

`POST .../time/heartbeat` comes every 30 seconds from the open case screen. It counts only when the tab is
visible and the person gave input in the last 5 minutes; the stage is the pack workflow step whose screens
include the current screen. A beat within 5 minutes of the person's latest entry for the same stage
extends it; otherwise it starts a new entry, so breaks are not counted. `GET .../time` gives the named
expert's time by workflow step, plus any adjustments; `PATCH .../time/adjust` (`invoice.create`) adds an
audited adjustment that never takes a step below zero.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.http import DbConn
from app.core.packs import MergedPack
from app.features.report.common import Pack, View, invalid
from app.features.report.router import router

IDLE_SECONDS = 300
BREAK = "5 minutes"

Adjust = Annotated[CaseAccess, Depends(require_case_access("invoice.create"))]


class HeartbeatIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    screen: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    visible: bool
    idle_seconds: int = Field(ge=0)


class HeartbeatOut(BaseModel):
    counted: bool
    stage: str | None = None


class AdjustIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    seconds: int = Field(ge=-360000, le=360000)
    reason: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")


class StageTime(BaseModel):
    stage: str
    seconds: int


class TimeOut(BaseModel):
    stages: list[StageTime]
    total_seconds: int
    adjusted: bool


def stage_for(pack: MergedPack, screen: str) -> str | None:
    for step in pack.workflow_steps():
        if screen in step.get("screens", []):
            return str(step["key"])
    return None


async def totals(conn: Any, case_id: uuid.UUID, pack: MergedPack) -> TimeOut:
    cur = await conn.execute(
        "select t.stage, sum(t.seconds), bool_or(t.adjusted_by_expert) from app.time_entries t"
        " join app.cases c on c.id = t.case_id"
        " where t.case_id = %s and (t.person_id = c.named_expert_id or t.adjusted_by_expert) group by t.stage",
        (case_id,),
    )
    rows = {stage: (int(seconds), bool(adjusted)) for stage, seconds, adjusted in await cur.fetchall()}
    keys = [str(s["key"]) for s in pack.workflow_steps()]
    stages = [StageTime(stage=k, seconds=rows.get(k, (0, False))[0]) for k in keys]
    return TimeOut(
        stages=stages,
        total_seconds=sum(s.seconds for s in stages),
        adjusted=any(a for _, a in rows.values()),
    )


@router.post("/cases/{case_id}/time/heartbeat", response_model=HeartbeatOut)
async def heartbeat(case_id: uuid.UUID, body: HeartbeatIn, access: View, conn: DbConn, pack: Pack) -> HeartbeatOut:
    stage = stage_for(pack, body.screen)
    if stage is None:
        raise invalid("screen")
    if not body.visible or body.idle_seconds > IDLE_SECONDS:
        return HeartbeatOut(counted=False)
    cur = await conn.execute(
        "select id, stage, ended_at >= now() - %s::interval from app.time_entries"
        " where case_id = %s and person_id = app.current_person_id() and not adjusted_by_expert"
        " order by ended_at desc limit 1",
        (BREAK, case_id),
    )
    latest = await cur.fetchone()
    if latest is not None and latest[1] == stage and latest[2]:
        await conn.execute(
            "update app.time_entries set ended_at = now(),"
            " seconds = greatest(0, extract(epoch from now() - started_at))::integer where id = %s",
            (latest[0],),
        )
    else:
        await conn.execute(
            "insert into app.time_entries (case_id, person_id, stage, started_at, ended_at, seconds)"
            " values (%s, app.current_person_id(), %s, now(), now(), 0)",
            (case_id, stage),
        )
    return HeartbeatOut(counted=True, stage=stage)


@router.get("/cases/{case_id}/time", response_model=TimeOut)
async def case_time(case_id: uuid.UUID, access: View, conn: DbConn, pack: Pack) -> TimeOut:
    return await totals(conn, case_id, pack)


@router.patch("/cases/{case_id}/time/adjust", response_model=TimeOut)
async def adjust_time(case_id: uuid.UUID, body: AdjustIn, access: Adjust, conn: DbConn, pack: Pack) -> TimeOut:
    before = await totals(conn, case_id, pack)
    current = {s.stage: s.seconds for s in before.stages}
    if body.stage not in current:
        raise invalid("stage")
    if current[body.stage] + body.seconds < 0:
        raise invalid("seconds")
    await conn.execute(
        "insert into app.time_entries (case_id, person_id, stage, started_at, ended_at, seconds,"
        " adjusted_by_expert, reason) values (%s, app.current_person_id(), %s, now(), now(), %s, true, %s)",
        (case_id, body.stage, body.seconds, body.reason),
    )
    await audit.record(conn, action="time.adjusted", target=f"time:{body.stage}", case_id=case_id)
    return await totals(conn, case_id, pack)
