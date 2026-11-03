"""Rule 6 for the temporary direct-API client (ADR 0036): page text, prompts, answers, identifiers and the
key never reach a log line or an error message, on success or failure. Only counts, durations, status
codes and request IDs are logged. The SDK's and httpx's debug logging (which prints request bodies and
headers) is held at WARNING. Everything answers with recorded HTTP; nothing reaches api.anthropic.com.
"""

from __future__ import annotations

import logging

import httpx2
import pytest

from app.core.ai.anthropic_live import LiveAnthropic
from app.core.ai.bedrock import ModelRequest
from app.core.config import ai_settings
from tests.support.ai import NAME, cell_env, mod11_number, spaced
from tests.support.live_ai import sse_events

pytestmark = pytest.mark.anyio

KEY = "sk-ant-test-not-real"
NUMBER = mod11_number("943476591")
SECRETS = (NUMBER, spaced(NUMBER), "Pemberton", NAME.split()[0] + " ", KEY)


def assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, "patient text or a key reached a log line"


def _request() -> ModelRequest:
    return ModelRequest(
        model_id="claude-sonnet-4-6",
        system=f"Label the page for {NAME}.",
        context={"subject": NAME},
        text=f"{NAME} {spaced(NUMBER)}",
        schema={"type": "object"},
    )


def _live(handler: httpx2.MockTransport) -> LiveAnthropic:
    return LiveAnthropic(
        ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY)), KEY, transport=handler
    )


async def test_the_direct_client_logs_tokens_and_status_never_prompts_answers_or_error_bodies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [
            {"type": "tool_use", "id": "t1", "name": "record_output", "input": {"who": NAME, "number": NUMBER}}
        ],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": 3, "cache_read_input_tokens": 7},
    }
    answers = [
        httpx2.Response(200, content=sse_events(message), headers={"content-type": "text/event-stream"}),
        httpx2.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": f"{NAME} {NUMBER}"}}
        ),
        httpx2.Response(
            429, json={"type": "error", "error": {"type": "rate_limit_error", "message": f"{NAME} {NUMBER}"}}
        ),
    ]
    live = _live(httpx2.MockTransport(lambda _req: answers.pop(0)))
    response = await live.invoke(_request())
    with pytest.raises(Exception) as refused:
        await live.invoke(_request())
    with pytest.raises(Exception) as busy:
        await live.invoke(_request())
    assert response.output["who"] == NAME
    assert any(r.name.startswith("app.ai") for r in caplog.records), "the client logs what it did"
    assert_clean(caplog.text)
    assert_clean(str(refused.value) + str(busy.value) + repr(live))
    assert_clean(repr(response) + repr(_request()))


async def test_a_dropped_connection_logs_no_url_or_body(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def boom(_req: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError(f"{NAME} {NUMBER}")

    live = _live(httpx2.MockTransport(boom))
    with pytest.raises(Exception) as caught:
        await live.invoke(_request())
    assert_clean(caplog.text)
    assert_clean(str(caught.value))


def test_the_sdk_and_httpx_never_log_request_bodies() -> None:
    import app.core.ai.anthropic_live  # noqa: F401  (pins the loggers on import)

    for name in ("anthropic", "httpx", "httpx2", "httpcore"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name


def _line(caplog: pytest.LogCaptureFixture) -> str:
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("direct call:")]
    assert len(lines) == 1, "one line per direct attempt"
    return lines[0]


async def test_a_failing_call_logs_its_class_status_error_type_and_request_id_and_nothing_of_the_text(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    body = {"type": "error", "error": {"type": "rate_limit_error", "message": f"{NAME} {NUMBER} {KEY}"}}
    live = _live(
        httpx2.MockTransport(
            lambda _req: httpx2.Response(429, json=body, headers={"request-id": "req_fail123", "retry-after": "7"})
        )
    )
    with pytest.raises(Exception) as busy:
        await live.invoke(_request())
    line = _line(caplog)
    for expected in (
        "provider=anthropic",
        "model=claude-sonnet-4-6",
        "outcome=busy",
        "error_class=ServiceBusy",
        "status=429",
        "error_type=rate_limit_error",
        "request_id=req_fail123",
        "retry_after=7",
    ):
        assert expected in line, expected
    assert_clean(caplog.text)
    assert_clean(str(busy.value) + repr(busy.value))


async def test_a_spend_limit_logs_its_error_code_from_the_typed_error_never_the_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    body = {
        "type": "error",
        "error": {
            "type": "invalid_request_error",
            "code": "enforced_spend_limit_reached",
            "message": f"{NAME} {NUMBER}",
        },
    }
    live = _live(
        httpx2.MockTransport(lambda _req: httpx2.Response(400, json=body, headers={"request-id": "req_spend"}))
    )
    with pytest.raises(Exception):  # noqa: B017, PT011
        await live.invoke(_request())
    line = _line(caplog)
    assert "outcome=unavailable" in line and "error_code=enforced_spend_limit_reached" in line
    assert "error_class=ModelUnavailable" in line and "status=400" in line
    assert_clean(caplog.text)


async def test_a_good_call_logs_its_request_id_tokens_and_cache_counts(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    message = {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "tool_use", "id": "t1", "name": "record_output", "input": {"who": NAME}}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {
            "input_tokens": 12,
            "output_tokens": 3,
            "cache_read_input_tokens": 700,
            "cache_creation_input_tokens": 90,
        },
    }
    headers = {"content-type": "text/event-stream", "request-id": "req_good456"}
    live = _live(httpx2.MockTransport(lambda _req: httpx2.Response(200, content=sse_events(message), headers=headers)))
    await live.invoke(_request())
    line = _line(caplog)
    for expected in (
        "outcome=ok",
        "in=12",
        "out=3",
        "cache_read=700",
        "cache_created=90",
        "stop=tool_use",
        "request_id=req_good456",
    ):
        assert expected in line, expected
    assert_clean(caplog.text)
