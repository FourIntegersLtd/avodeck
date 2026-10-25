"""POST /v1/help-requests: the Help form emails what the expert types to our own support address.

ADR 0022: the one place free text goes into an email. The text goes only into the email: it is never
stored, logged or audited (the audit row names the action and nothing else). The address comes from the
cell's config and is never sent to the browser; replies go to the expert.
"""

from __future__ import annotations

import logging
from typing import Annotated, Literal

from fastapi import Request
from pydantic import AfterValidator, BaseModel, StringConstraints

from app.core import audit
from app.core.config import support_email
from app.core.email import _APP_PATH, _NO_CONTROL, Mailer, help_request_mail
from app.core.errors import ApiError
from app.core.http import CurrentIdentity, DbConn, router

logger = logging.getLogger("app.help")

help_router = router(prefix="/v1", tags=["help"])

DAILY_LIMIT = 5


def _no_control(value: str) -> str:
    if _NO_CONTROL.search(value):
        raise ValueError("control characters")
    return value


class HelpRequest(BaseModel):
    topic: Literal["case", "account", "billing", "other"]
    message: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000), AfterValidator(_no_control)
    ]
    page: Annotated[str, StringConstraints(pattern=_APP_PATH.pattern)]

    def __repr__(self) -> str:  # never show the text
        return "HelpRequest(...)"


@help_router.post("/help-requests", status_code=202)
async def send_help_request(body: HelpRequest, request: Request, identity: CurrentIdentity, conn: DbConn) -> None:
    """Emails the message to our support address; at most 5 per person per day."""
    to = support_email()
    # One at a time per person, so messages sent together cannot all slip under the limit.
    await conn.execute("select pg_advisory_xact_lock(hashtextextended('help:' || app.current_person_id()::text, 0))")
    cur = await conn.execute(
        "select count(*) from app.audit_log where person_id = app.current_person_id()"
        " and action = 'help.request_sent' and at > now() - interval '1 day'"
    )
    row = await cur.fetchone()
    if row is not None and row[0] >= DAILY_LIMIT:
        raise ApiError("help_limit", "Too many messages today.", 429)
    await audit.record(conn, action="help.request_sent", target="help")
    mailer: Mailer = request.app.state.mailer
    try:
        await mailer.send(
            help_request_mail(to, from_email=identity.email, topic=body.topic, message=body.message, page=body.page)
        )
    except Exception as exc:  # noqa: BLE001 - not sent: the count rolls back; the type only (rule 6)
        logger.warning("help request not sent: %s", type(exc).__name__)
        raise ApiError("help_not_sent", "The message could not be sent.", 503) from None
