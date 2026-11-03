"""Rule 6 for the temporary direct-API client (ADR 0036): page text, prompts, answers, identifiers and the
key never reach a log line or an error message, on success or failure. Only counts, durations, status
codes and request IDs are logged. The SDK's and httpx's debug logging (which prints request bodies and
headers) is held at WARNING. Everything answers with recorded HTTP; nothing reaches api.anthropic.com.
"""

from __future__ import annotations

import logging

import httpx
import pytest

from app.core.ai.anthropic_live import LiveAnthropic
from app.core.ai.bedrock import ModelRequest
from app.core.config import ai_settings
from tests.support.ai import NAME, cell_env, mod11_number, spaced

pytestmark = pytest.mark.anyio

KEY = "sk-ant-test-not-real"
NUMBER = mod11_number("943476591")
SECRETS = (NUMBER, spaced(NUMBER), "Pemberton", NAME.split()[0] + " ", KEY)


def assert_clean(text: str) -> None:
    for secret in SECRETS:
        assert secret not in text, "patient text or a key reached a log line"


def _request() -> ModelRequest:
    return ModelRequest(
        model_id="claude-haiku-5-5",
        system=f"Label the page for {NAME}.",
        context={"subject": NAME},
        text=f"{NAME} {spaced(NUMBER)}",
        schema={"type": "object"},
    )


def _live(handler: httpx.MockTransport) -> LiveAnthropic:
    return LiveAnthropic(
        ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY)), KEY, transport=handler
    )


async def test_the_direct_client_logs_tokens_and_status_never_prompts_answers_or_error_bodies(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    answers = [
        httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-haiku-5-5",
                "content": [
                    {"type": "tool_use", "id": "t1", "name": "record_output", "input": {"who": NAME, "number": NUMBER}}
                ],
                "stop_reason": "tool_use",
                "stop_sequence": None,
                "usage": {"input_tokens": 12, "output_tokens": 3},
            },
        ),
        httpx.Response(
            400, json={"type": "error", "error": {"type": "invalid_request_error", "message": f"{NAME} {NUMBER}"}}
        ),
        httpx.Response(
            429, json={"type": "error", "error": {"type": "rate_limit_error", "message": f"{NAME} {NUMBER}"}}
        ),
    ]
    live = _live(httpx.MockTransport(lambda _req: answers.pop(0)))
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

    def boom(_req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"{NAME} {NUMBER}")

    live = _live(httpx.MockTransport(boom))
    with pytest.raises(Exception) as caught:
        await live.invoke(_request())
    assert_clean(caplog.text)
    assert_clean(str(caught.value))


def test_the_sdk_and_httpx_never_log_request_bodies() -> None:
    import app.core.ai.anthropic_live  # noqa: F401  (pins the loggers on import)

    for name in ("anthropic", "httpx"):
        assert logging.getLogger(name).getEffectiveLevel() >= logging.WARNING, name
