"""Email is for what matters: acting, the person's own security, a permanent record. Stage news stays in the app."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import pytest

from app.core import notifications
from app.core.email import OutboxMailer
from app.core.notifications import emails_at_once, notify

pytestmark = pytest.mark.anyio

# The kinds the app sends today (pipeline, close, after serving, organisations).
KINDS_IN_USE = ("bundle_ready", "deletion_paused", "case.reassigned", "deadline.court_order", "deadline.questions")


def test_no_stage_or_progress_kind_in_use_emails_at_once() -> None:
    for kind in KINDS_IN_USE:
        assert not emails_at_once(kind), kind


def test_a_kind_on_the_list_emails_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifications, "EMAIL_KINDS", frozenset({"zz.must_act"}))
    assert emails_at_once("zz.must_act") and not emails_at_once("zz.other")


class _Cursor:
    def __init__(self, row: tuple[object, ...]) -> None:
        self.row = row

    async def fetchone(self) -> tuple[object, ...]:
        return self.row


class _Conn:
    """What `app.notify` answers when the person's own choices say: tell me in the app and by email at once."""

    def __init__(self) -> None:
        self.id = uuid.uuid4()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        yield

    async def execute(self, sql: str, params: tuple[object, ...] = ()) -> _Cursor:
        return _Cursor((self.id, "person@example.test"))


async def test_a_stage_message_is_kept_in_the_app_and_sends_no_mail() -> None:
    mailer = OutboxMailer()
    conn = _Conn()
    got = await notify(conn, mailer, person_id=uuid.uuid4(), organisation_id=uuid.uuid4(), kind="bundle_ready")  # type: ignore[arg-type]
    assert got == conn.id and mailer.outbox == []


async def test_a_listed_kind_sends_one_mail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifications, "EMAIL_KINDS", frozenset({"zz.must_act"}))
    mailer = OutboxMailer()
    await notify(_Conn(), mailer, person_id=uuid.uuid4(), organisation_id=uuid.uuid4(), kind="zz.must_act")  # type: ignore[arg-type]
    assert [m.params for m in mailer.outbox] == [{"kind": "zz.must_act"}]
