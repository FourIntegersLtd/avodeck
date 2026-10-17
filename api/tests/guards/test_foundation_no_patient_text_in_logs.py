"""Rule 6: no patient text in logs (architecture 9 "Logs", brief acceptance test).

A request carrying a made-up identifier number and a made-up name, in its path, query string,
body and in an exception it causes, leaves neither in any log line. Logs keep the route template,
status and timing; exceptions keep their type and stack, never their message. Error reports to
Sentry are scrubbed the same way (tested with a capturing transport, no real DSN).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import sentry_sdk
from fastapi import APIRouter, FastAPI
from sentry_sdk.envelope import Envelope
from sentry_sdk.transport import Transport

from app.core import logs
from app.core.auth.supabase import SupabaseAuth
from app.core.config import auth_settings
from app.core.db import Database
from app.core.email import OutboxMailer
from app.main import create_app, mount
from tests.support.ai import NAME, mod11_number, spaced

pytestmark = pytest.mark.anyio

NUMBER = mod11_number("943476591")
SECRETS = (NUMBER, spaced(NUMBER), "Pemberton", NAME.split()[0] + " ")


def add_failing_route(app: FastAPI) -> None:
    router = APIRouter()

    @router.post("/v1/test-only/explode/{label}")
    async def explode(label: str, payload: dict[str, Any]) -> None:
        logging.getLogger("app.test").info("received number %s", payload["number"])
        raise RuntimeError(f"could not handle {payload['name']} {payload['number']} {label}")

    mount(app, router)


async def send_requests(app: FastAPI) -> list[httpx.Response]:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="https://test") as client:
        return [
            await client.post(
                f"/v1/test-only/explode/{NAME}", json={"name": NAME, "number": spaced(NUMBER)}, params={"q": NUMBER}
            ),
            await client.get(f"/v1/cases/{uuid.uuid4()}", params={"q": f"{NAME} {NUMBER}"}),
            await client.post("/v1/auth/sign-in", json={"email": NAME, "password": NUMBER}),
        ]


def assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, "patient text reached a log line"


async def test_a_request_with_an_identifier_and_a_name_leaves_neither_in_any_log_line(
    app: FastAPI, caplog: pytest.LogCaptureFixture
) -> None:
    add_failing_route(app)
    caplog.set_level(logging.DEBUG)
    failed, *_ = await send_requests(app)
    assert failed.status_code == 500
    assert failed.json()["error"]["code"] == "server_error"
    assert_clean(failed.text)

    records = caplog.records
    assert any(r.name == "app.request" for r in records), "requests are logged (route, status, timing)"
    assert any("RuntimeError" in (r.exc_text or "") for r in records), "the failure is logged with its type"
    assert any(r.name == "app.test" for r in records)
    assert_clean(caplog.text)
    for record in records:
        assert_clean(record.getMessage())
        assert_clean(record.exc_text or "")


def test_the_scrubber_removes_pack_identifiers_long_numbers_query_strings_and_addresses() -> None:
    line = f"GET /v1/cases?q=secret+words {spaced(NUMBER)} ref 98765432101 from ada@example.test ZZ-ID-123456"
    scrubbed = logs.scrub(line)
    for gone in ("secret", spaced(NUMBER), "98765432101", "ada@example.test", "ZZ-ID-123456"):
        assert gone not in scrubbed
    assert scrubbed.startswith("GET /v1/cases")


class CapturingTransport(Transport):
    sent: list[bytes] = []

    def capture_envelope(self, envelope: Envelope) -> None:
        CapturingTransport.sent.append(envelope.serialize())


@pytest.fixture
def sentry() -> Iterator[list[bytes]]:
    CapturingTransport.sent = []
    logs.init_sentry("https://public@sentry.invalid/1", environment="test", transport=CapturingTransport)
    try:
        yield CapturingTransport.sent
    finally:
        sentry_sdk.init(dsn=None)


def test_sentry_is_configured_to_send_no_personal_data() -> None:
    options = logs.sentry_options("https://public@sentry.invalid/1", environment="test")
    assert options["send_default_pii"] is False
    assert options["include_local_variables"] is False
    assert options["max_request_body_size"] == "never"
    assert callable(options["before_send"]) and callable(options["before_breadcrumb"])


async def test_error_reports_carry_neither_the_identifier_nor_the_name(
    sentry: list[bytes], api_db: Database, auth_env: dict[str, str]
) -> None:
    app = create_app(database=api_db, region="zz", auth=SupabaseAuth(auth_settings(auth_env)), mailer=OutboxMailer())
    add_failing_route(app)
    await send_requests(app)
    sentry_sdk.flush()
    sent = b"\n".join(sentry)
    assert b"RuntimeError" in sent, "the failure reached the error report"
    for secret in SECRETS:
        assert secret.encode() not in sent
