"""Mails about Avodeck support's actions (rule 6: Resend gets no patient text).

- `support.job_resumed` / `support.job_requeued`: support set one of the case's jobs going again; the
  case's and the job's reference ids only.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.email import (
    SUPPORT_JOB_REQUEUED,
    SUPPORT_JOB_RESUMED,
    TEMPLATES,
    Mail,
    OutboxMailer,
    support_job_mail,
)

pytestmark = pytest.mark.anyio

CASE = uuid.UUID("00000000-0000-4000-8000-000000000001")
JOB = uuid.UUID("00000000-0000-4000-8000-0000000000cc")
TO = "zz-someone@example.test"


def test_the_templates_are_known() -> None:
    assert (SUPPORT_JOB_RESUMED, SUPPORT_JOB_REQUEUED) == ("support.job_resumed", "support.job_requeued")
    assert {SUPPORT_JOB_RESUMED, SUPPORT_JOB_REQUEUED} <= TEMPLATES


@pytest.mark.parametrize("template", [SUPPORT_JOB_RESUMED, SUPPORT_JOB_REQUEUED])
async def test_a_job_mail_carries_the_case_and_job_ids_only(template: str) -> None:
    mail = support_job_mail(TO, template=template, case_id=CASE, job_id=JOB)
    assert mail.params == {"case_ref": str(CASE), "job_ref": str(JOB)}
    outbox = OutboxMailer()
    await outbox.send(mail)
    assert outbox.outbox == [mail]


@pytest.mark.parametrize(
    "params",
    [
        {"case_ref": str(CASE)},
        {"case_ref": str(CASE), "job_ref": "the scan of the letter"},
        {"case_ref": str(CASE), "job_ref": str(JOB), "reason": "fault_fixed"},
    ],
)
async def test_the_outbox_refuses_a_job_mail_with_anything_else(params: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        await OutboxMailer().send(Mail(TO, SUPPORT_JOB_RESUMED, params))


def test_only_the_job_templates_go_through_the_job_mail() -> None:
    with pytest.raises(ValueError):
        support_job_mail(TO, template="org.invite", case_id=CASE, job_id=JOB)
