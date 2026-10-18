"""Screen 18: mark a signed version served, add servings and corrections, and withdraw a served version.

`POST .../versions/{v}/serve` (named expert, step-up) needs a locked version (409 `not_locked`,
`already_served`): it records the first serving (`served_on`, no later than today in the pack's timezone,
and `sent_via`), moves the version to served and the case to served, writes to the audit log and emits
`report_served`. `GET/POST .../servings` list servings and add another one or a correction (with a
reason) to a served version (409 `not_served`). `POST .../withdraw` (step-up) withdraws a served version
with a reason; it stays locked and readable (rule 8). Avodeck records the serving; the expert sends the
files themselves (emails carry no patient content).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from fastapi import BackgroundTasks
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core import audit
from app.core.contracts import events
from app.core.errors import ApiError
from app.core.http import DbConn
from app.features.report.common import Pack, Serve, View, Withdraw, invalid, not_found
from app.features.report.router import router

SentVia = Literal["email", "post", "portal", "hand", "other"]


class ServeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    served_on: date
    sent_via: SentVia
    recipient_party_id: uuid.UUID | None = None


class ServingIn(ServeIn):
    correction_of: uuid.UUID | None = None
    reason: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{0,39}$")

    @model_validator(mode="after")
    def _paired(self) -> ServingIn:
        if (self.correction_of is None) != (self.reason is None):
            raise ValueError("a correction names the serving it corrects and a reason")
        return self


class WithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$")


class ServingOut(BaseModel):
    id: uuid.UUID
    served_on: date
    sent_via: str
    recipient_party_id: uuid.UUID | None
    correction_of: uuid.UUID | None
    reason: str | None
    created_at: datetime


class ServingsOut(BaseModel):
    servings: list[ServingOut]


class VersionStatus(BaseModel):
    id: uuid.UUID
    status: str


_SERVING = "id, served_on, sent_via, recipient_party_id, correction_of, reason, created_at"


async def _status(conn: Any, case_id: uuid.UUID, version_id: uuid.UUID) -> str:
    cur = await conn.execute(
        "select status from app.report_versions where id = %s and case_id = %s", (version_id, case_id)
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    return str(row[0])


def _not_future(served_on: date, timezone: str) -> None:
    if served_on > datetime.now(ZoneInfo(timezone)).date():
        raise invalid("served_on")


async def _add_serving(conn: Any, case_id: uuid.UUID, version_id: uuid.UUID, body: ServeIn) -> ServingOut:
    correction = body.correction_of if isinstance(body, ServingIn) else None
    reason = body.reason if isinstance(body, ServingIn) else None
    if correction is not None:
        cur = await conn.execute(
            "select 1 from app.report_servings where id = %s and report_version_id = %s", (correction, version_id)
        )
        if await cur.fetchone() is None:
            raise invalid("correction_of")
    if body.recipient_party_id is not None:
        cur = await conn.execute(
            "select 1 from app.case_parties where id = %s and case_id = %s", (body.recipient_party_id, case_id)
        )
        if await cur.fetchone() is None:
            raise invalid("recipient_party_id")
    cur = await conn.execute(
        "insert into app.report_servings (case_id, report_version_id, recipient_party_id, served_on, sent_via,"
        f" correction_of, reason) values (%s, %s, %s, %s, %s, %s, %s) returning {_SERVING}",
        (case_id, version_id, body.recipient_party_id, body.served_on, body.sent_via, correction, reason),
    )
    row = await cur.fetchone()
    assert row is not None
    return ServingOut(**dict(zip(ServingOut.model_fields, row, strict=True)))


@router.post("/cases/{case_id}/report/versions/{version_id}/serve", response_model=VersionStatus)
async def serve_version(
    case_id: uuid.UUID,
    version_id: uuid.UUID,
    body: ServeIn,
    access: Serve,
    conn: DbConn,
    pack: Pack,
    after_commit: BackgroundTasks,
) -> VersionStatus:
    status = await _status(conn, case_id, version_id)
    if status == "draft":
        raise ApiError("not_locked", "Sign this version first.", 409)
    if status != "locked":
        raise ApiError("already_served", "This version has already been served.", 409)
    _not_future(body.served_on, pack.timezone)
    await conn.execute("update app.report_versions set status = 'served' where id = %s", (version_id,))
    await _add_serving(conn, case_id, version_id, body)
    await conn.execute("update app.cases set status = 'served' where id = %s and status <> 'served'", (case_id,))
    await audit.record(conn, action="report.served", target=f"report_version:{version_id}", case_id=case_id)
    # Background tasks run once the response is ready, after the request's transaction committed.
    by = await (await conn.execute("select app.current_person_id()")).fetchone()
    served = events.ReportServed(case_id, version_id, body.served_on, by[0] if by else None)
    after_commit.add_task(events.events.emit, served)
    return VersionStatus(id=version_id, status="served")


@router.get("/cases/{case_id}/report/versions/{version_id}/servings", response_model=ServingsOut)
async def list_servings(case_id: uuid.UUID, version_id: uuid.UUID, access: View, conn: DbConn) -> ServingsOut:
    await _status(conn, case_id, version_id)
    cur = await conn.execute(
        f"select {_SERVING} from app.report_servings where report_version_id = %s order by created_at, id",
        (version_id,),
    )
    return ServingsOut(
        servings=[ServingOut(**dict(zip(ServingOut.model_fields, r, strict=True))) for r in await cur.fetchall()]
    )


@router.post("/cases/{case_id}/report/versions/{version_id}/servings", status_code=201, response_model=ServingOut)
async def add_serving(
    case_id: uuid.UUID, version_id: uuid.UUID, body: ServingIn, access: Serve, conn: DbConn, pack: Pack
) -> ServingOut:
    if await _status(conn, case_id, version_id) != "served":
        raise ApiError("not_served", "Only a served version has servings.", 409)
    _not_future(body.served_on, pack.timezone)
    out = await _add_serving(conn, case_id, version_id, body)
    await audit.record(conn, action="report.serving_added", target=f"report_version:{version_id}", case_id=case_id)
    return out


@router.post("/cases/{case_id}/report/versions/{version_id}/withdraw", response_model=VersionStatus)
async def withdraw_version(
    case_id: uuid.UUID, version_id: uuid.UUID, body: WithdrawIn, access: Withdraw, conn: DbConn
) -> VersionStatus:
    if await _status(conn, case_id, version_id) != "served":
        raise ApiError("not_served", "Only a served version can be withdrawn.", 409)
    await conn.execute(
        "update app.report_versions set status = 'withdrawn', withdrawn_reason = %s where id = %s",
        (body.reason, version_id),
    )
    await audit.record(conn, action="report.withdrawn", target=f"report_version:{version_id}", case_id=case_id)
    return VersionStatus(id=version_id, status="withdrawn")
