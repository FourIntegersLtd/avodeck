"""Screen 18b: time spent on the case, counted quietly for the invoice.

`POST .../time/heartbeat` comes every 30 seconds from the open case screen. It counts only when the tab is
visible and the person gave input in the last 5 minutes; the stage is the pack workflow step whose screens
include the current screen. A beat within 5 minutes of the person's latest entry for the same stage
extends it; otherwise it starts a new entry, so breaks are not counted. `GET .../time` gives the named
expert's time by workflow step, plus any adjustments; `PATCH .../time/adjust` (`invoice.create`) adds an
audited adjustment that never takes a step below zero.

`POST .../time/estimates` (`invoice.create`) records the expert's estimate for one or more workflow steps
(hours, with an optional fixed fee) as new rows; the newest row per step is the estimate. `GET .../time`
gives each step's estimate beside its time and, once the time on the estimated steps runs over their estimate
by more than the jurisdiction pack's `estimate_overrun_percent`, a notice naming the instructing party in the
pack's word. The notice never blocks anything.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
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


class EstimateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    stage: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")
    hours: Annotated[Decimal, Field(ge=0, max_digits=7, decimal_places=2)]
    fee: Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)] | None = None


class EstimatesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimates: list[EstimateIn] = Field(min_length=1, max_length=40)


class StageTime(BaseModel):
    stage: str
    seconds: int
    estimate_hours: Decimal | None = None
    estimate_fee: Decimal | None = None


class OverEstimate(BaseModel):
    percent: int
    instructing_party: str


class TimeOut(BaseModel):
    stages: list[StageTime]
    total_seconds: int
    adjusted: bool
    estimate_hours: Decimal | None = None
    over_estimate: OverEstimate | None = None


def stage_for(pack: MergedPack, screen: str) -> str | None:
    for step in pack.workflow_steps():
        if screen in step.get("screens", []):
            return str(step["key"])
    return None


async def latest_estimates(conn: Any, case_id: uuid.UUID) -> dict[str, tuple[Decimal, Decimal | None]]:
    """The newest estimate per workflow step: (hours, fixed fee or None)."""
    cur = await conn.execute(
        "select distinct on (stage) stage, hours, fee from app.time_estimates where case_id = %s"
        " order by stage, created_at desc, id",
        (case_id,),
    )
    rows = await cur.fetchall()
    return {str(stage): (Decimal(hours), None if fee is None else Decimal(fee)) for stage, hours, fee in rows}


async def estimate_amount(conn: Any, case_id: uuid.UUID, rate: Decimal) -> Decimal | None:
    """The estimate in money: each step's fixed fee, or its hours at the rate. None without an estimate."""
    found = await latest_estimates(conn, case_id)
    if not found:
        return None
    total = sum((fee if fee is not None else hours * rate for hours, fee in found.values()), Decimal(0))
    return total.quantize(Decimal("0.01"))


def _over(pack: MergedPack, stages: list[StageTime]) -> OverEstimate | None:
    percent = pack.estimate_overrun_percent()
    estimated = [s for s in stages if s.estimate_hours is not None]
    if percent is None or not estimated:
        return None
    seconds = sum(s.seconds for s in estimated)
    allowed = sum(Decimal(s.estimate_hours or 0) for s in estimated) * 3600 * (100 + percent)
    if Decimal(seconds) * 100 <= allowed:
        return None
    parties = pack.instructing_party_types()
    return OverEstimate(percent=percent, instructing_party=str(parties[0]["label"]) if parties else "")


async def totals(conn: Any, case_id: uuid.UUID, pack: MergedPack) -> TimeOut:
    cur = await conn.execute(
        "select t.stage, sum(t.seconds), bool_or(t.adjusted_by_expert) from app.time_entries t"
        " join app.cases c on c.id = t.case_id"
        " where t.case_id = %s and (t.person_id = c.named_expert_id or t.adjusted_by_expert) group by t.stage",
        (case_id,),
    )
    rows = {stage: (int(seconds), bool(adjusted)) for stage, seconds, adjusted in await cur.fetchall()}
    keys = [str(s["key"]) for s in pack.workflow_steps()]
    estimates = await latest_estimates(conn, case_id)
    stages = [
        StageTime(
            stage=k,
            seconds=rows.get(k, (0, False))[0],
            estimate_hours=estimates[k][0] if k in estimates else None,
            estimate_fee=estimates[k][1] if k in estimates else None,
        )
        for k in keys
    ]
    shown = [s.estimate_hours for s in stages if s.estimate_hours is not None]
    return TimeOut(
        stages=stages,
        total_seconds=sum(s.seconds for s in stages),
        adjusted=any(a for _, a in rows.values()),
        estimate_hours=sum(shown, Decimal(0)).quantize(Decimal("0.01")) if shown else None,
        over_estimate=_over(pack, stages),
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


@router.post("/cases/{case_id}/time/estimates", response_model=TimeOut)
async def add_estimates(case_id: uuid.UUID, body: EstimatesIn, access: Adjust, conn: DbConn, pack: Pack) -> TimeOut:
    keys = {str(s["key"]) for s in pack.workflow_steps()}
    stages = [e.stage for e in body.estimates]
    if any(stage not in keys for stage in stages) or len(set(stages)) != len(stages):
        raise invalid("stage")
    for e in body.estimates:
        await conn.execute(
            "insert into app.time_estimates (case_id, stage, hours, fee) values (%s, %s, %s, %s)",
            (case_id, e.stage, e.hours, e.fee),
        )
    await audit.record(conn, action="time.estimated", target=f"case:{case_id}", case_id=case_id)
    return await totals(conn, case_id, pack)
