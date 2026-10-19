"""Screen 19: the experts' discussion and trial pack steps, read from the trial slice through its contract."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel

from app.core.contracts import trial_steps
from app.core.http import DbConn
from app.features.after_serving.common import View
from app.features.after_serving.router import router


class MeetingStepOut(BaseModel):
    id: uuid.UUID
    type: str
    discussion_on: date
    sign_by: date
    status: Literal["draft", "out_for_signature", "signed", "signed_external"]


class PackStepOut(BaseModel):
    built_on: date
    stale: bool


class AfterServingSteps(BaseModel):
    meetings: list[MeetingStepOut]
    pack: PackStepOut | None


@router.get("/cases/{case_id}/after-serving/steps", response_model=AfterServingSteps)
async def steps(case_id: uuid.UUID, access: View, conn: DbConn) -> AfterServingSteps:
    pack = await trial_steps.latest_pack_step(conn, case_id)
    return AfterServingSteps(
        meetings=[MeetingStepOut(**m.__dict__) for m in await trial_steps.meeting_steps(conn, case_id)],
        pack=PackStepOut(built_on=pack.built_on, stale=pack.stale) if pack else None,
    )
