"""Avodeck support's operator actions (support-account plan phase 3; permissions.md "Avodeck staff").

Every route here is `/v1/support/...`, needs current support staff with MFA in the last 10 minutes
(`require_support_staff`: anyone else 404, a stale MFA 403 `step_up_required`), is behind its own flag
(off until staff switch it on; 404 while off), takes a reason code from a fixed list and nothing else,
answers with ids and codes only, writes `support.<action>` to the audit trail with ids and the reason
code (rule 6), and tells the affected expert by a patient-free email when it worked.

- `POST /v1/support/jobs/{job_id}/resume` (flag `support_job_resume`): a paused job goes back on the
  queue; the worker moves it paused -> running. 409 `job_not_paused`, `case_on_hold`.
- `POST /v1/support/jobs/{job_id}/requeue` (flag `support_job_requeue`): a failed job goes failed ->
  queued with attempts 0; what it was stays in `job_support_actions`. 409 `job_not_failed`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Literal

from fastapi import Depends, Request
from pydantic import BaseModel, ConfigDict

from app.core import audit, jobs
from app.core.auth.support import SupportStaff, require_support_staff
from app.core.email import SUPPORT_JOB_REQUEUED, SUPPORT_JOB_RESUMED, support_job_mail
from app.core.errors import ApiError
from app.core.flags import require_flag
from app.core.http import DbConn, router
from app.core.notifications import send_mail

support_router = router(prefix="/v1/support", tags=["support"])

Staff = Annotated[SupportStaff, Depends(require_support_staff)]

JOB_RESUME_FLAG = "support_job_resume"
JOB_REQUEUE_FLAG = "support_job_requeue"

JobReason = Literal["service_restored", "fault_fixed", "expert_asked"]

_REFUSED = {
    "job_not_paused": "Only a paused job can be resumed.",
    "job_not_failed": "Only a failed job can be put back on the queue.",
    "case_on_hold": "The case is on hold; its jobs wait until it resumes.",
}


class JobActionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason_code: JobReason


class JobActionResult(BaseModel):
    job_id: uuid.UUID
    case_id: uuid.UUID
    action: Literal["resume", "requeue"]
    status: str


async def _job_action(
    request: Request, conn: DbConn, job_id: uuid.UUID, action: Literal["resume", "requeue"], reason: str
) -> JobActionResult:
    done = await jobs.support_job_action(conn, job_id, action, reason)
    if done.outcome == "not_found" or done.case_id is None:
        raise ApiError("not_found", "Not found.", 404)
    if done.outcome != "ok":
        raise ApiError(done.outcome, _REFUSED[done.outcome], 409)
    audited = "support.job_resumed" if action == "resume" else "support.job_requeued"
    await audit.record(conn, action=audited, target=f"job:{job_id}/reason:{reason}", case_id=done.case_id)
    if done.expert_email:
        template = SUPPORT_JOB_RESUMED if action == "resume" else SUPPORT_JOB_REQUEUED
        mail = support_job_mail(done.expert_email, template=template, case_id=done.case_id, job_id=job_id)
        await send_mail(request.app.state.mailer, mail)
    return JobActionResult(job_id=job_id, case_id=done.case_id, action=action, status=done.status or "")


@support_router.post(
    "/jobs/{job_id}/resume", response_model=JobActionResult, dependencies=[Depends(require_flag(JOB_RESUME_FLAG))]
)
async def resume_job(
    request: Request, job_id: uuid.UUID, body: JobActionBody, conn: DbConn, _: Staff
) -> JobActionResult:
    return await _job_action(request, conn, job_id, "resume", body.reason_code)


@support_router.post(
    "/jobs/{job_id}/requeue", response_model=JobActionResult, dependencies=[Depends(require_flag(JOB_REQUEUE_FLAG))]
)
async def requeue_job(
    request: Request, job_id: uuid.UUID, body: JobActionBody, conn: DbConn, _: Staff
) -> JobActionResult:
    return await _job_action(request, conn, job_id, "requeue", body.reason_code)
