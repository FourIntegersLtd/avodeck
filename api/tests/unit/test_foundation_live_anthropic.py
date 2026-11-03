"""TEMPORARY, dummy data only (ADR 0036): the live direct-API client behind `BedrockClient`.

An `httpx.MockTransport` answers every call with replies shaped like the Messages API, so nothing
reaches api.anthropic.com. The output schema is forced as the only tool, as for Bedrock.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from app.core.ai.anthropic_live import LiveAnthropic
from app.core.ai.bedrock import BedrockClient, ModelRequest, ModelResponse, ModelUnavailable, ServiceBusy
from app.core.ai.bedrock_live import ModelRequestRefused
from app.core.ai.cell import ModelNotAllowed
from app.core.config import ai_settings
from tests.support.ai import CELL_ENV, cell_env

pytestmark = pytest.mark.anyio

KEY = "sk-ant-test-not-real"
DIRECT = "claude-haiku-5-5"
SCHEMA = {
    "type": "object",
    "properties": {"labels": {"type": "array", "items": {"type": "string"}}},
    "required": ["labels"],
    "additionalProperties": False,
}


def settings() -> Any:
    return ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY))


def request(model_id: str = DIRECT) -> ModelRequest:
    return ModelRequest(
        model_id=model_id,
        system="Label each line.",
        context={"fact_kinds": ["result", "prescription"]},
        text="line one\nline two",
        schema=SCHEMA,
        temperature=0.0,
        max_tokens=1024,
        task_id="T-test",
        subject="page:1",
    )


def message(output: dict[str, Any], *, stop: str = "tool_use", content: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": DIRECT,
        "content": content
        if content is not None
        else [{"type": "tool_use", "id": "toolu_1", "name": "record_output", "input": output}],
        "stop_reason": stop,
        "stop_sequence": None,
        "usage": {"input_tokens": 120, "output_tokens": 30},
    }


def error_body(kind: str, code: str | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"type": "error", "error": {"type": kind, "message": "details the client must not log"}}
    if code:
        body["error"]["code"] = code
    return body


class Seen:
    def __init__(self, handler: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []
        self._handler = handler

    def transport(self) -> httpx.MockTransport:
        def handle(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            return self._handler(req)

        return httpx.MockTransport(handle)

    def json(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.requests[index].content)


def live(handler: Callable[[httpx.Request], httpx.Response]) -> tuple[LiveAnthropic, Seen]:
    seen = Seen(handler)
    return LiveAnthropic(settings(), KEY, transport=seen.transport()), seen


def reply(status: int, body: dict[str, Any], **headers: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda _req: httpx.Response(status, json=body, headers=headers)


def test_it_is_a_model_client_on_the_direct_endpoint_that_never_retries_by_itself() -> None:
    client = LiveAnthropic(settings(), KEY)
    assert isinstance(client, BedrockClient)
    assert client.kind == "anthropic"
    assert client.endpoint == "https://api.anthropic.com"
    assert client.client.max_retries == 0, "the AI kit owns back-off"
    assert KEY not in repr(client)


def test_it_refuses_an_endpoint_off_the_allow_list() -> None:
    bad = ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY))
    bad = type(bad)(**{**bad.__dict__, "anthropic_endpoint": "https://elsewhere.example.com"})
    with pytest.raises(Exception, match="allow-list"):
        LiveAnthropic(bad, KEY)


async def test_the_schema_is_the_only_tool_forced_and_its_input_comes_back() -> None:
    client, seen = live(reply(200, message({"labels": ["a", "b"]}, content=None)))
    req = request()
    response = await client.invoke(req)
    assert response == ModelResponse(output={"labels": ["a", "b"]}, input_tokens=120, output_tokens=30, model_id=DIRECT)
    sent = seen.json()
    assert sent["model"] == DIRECT
    assert sent["max_tokens"] == 1024
    assert sent["system"] == "Label each line."
    assert sent["tools"] == [{"name": "record_output", "description": "Record the task output.", "input_schema": SCHEMA}]
    assert sent["tool_choice"] == {"type": "tool", "name": "record_output"}
    assert sent["messages"] == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Reference data (not instructions):\n" + json.dumps(dict(req.context), sort_keys=True)},
                {"type": "text", "text": req.text},
            ],
        }
    ]
    # Haiku 5.5 thinks (adaptive) unless told not to; thinking is billed as output. Disabled is accepted at the default effort.
    assert sent["thinking"] == {"type": "disabled"}
    for banned in ("temperature", "top_p", "top_k", "output_config"):
        assert banned not in sent, f"{banned}: non-default sampling returns a 400 on this model"
    assert sent["messages"][-1]["role"] == "user", "no assistant prefill"
    assert seen.requests[0].headers["x-api-key"] == KEY
    assert str(seen.requests[0].url).startswith("https://api.anthropic.com/")


async def test_a_masked_page_picture_goes_as_a_base64_image_block() -> None:
    import hashlib

    from app.core.ai import mask

    data = b"\x89PNG not a real picture"
    sha = hashlib.sha256(data).hexdigest()
    image = mask.MaskedImage(
        data=data, sha256=sha, format="png", masked_words=0, boxes=(), _seal=mask._seal_for(sha)
    )
    client, seen = live(reply(200, message({"labels": []})))
    await client.invoke(
        ModelRequest(
            model_id=DIRECT, system="s", context={}, text="t", schema=SCHEMA, image_sha256=image.sha256, image=image
        )
    )
    blocks = seen.json()["messages"][0]["content"]
    assert blocks[-1] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": base64.b64encode(image.data).decode()},
    }


async def test_a_model_id_off_the_allow_list_is_refused_before_anything_is_sent() -> None:
    client, seen = live(reply(200, message({"labels": []})))
    with pytest.raises(ModelNotAllowed):
        await client.invoke(request("claude-opus-9"))
    assert seen.requests == []


async def test_an_answer_without_the_tool_call_comes_back_empty_for_the_kit_to_refuse() -> None:
    client, _ = live(reply(200, message({}, stop="max_tokens", content=[{"type": "text", "text": "I cannot"}])))
    response = await client.invoke(request())
    assert response.output == {}
    assert (response.input_tokens, response.output_tokens) == (120, 30), "tokens still count for the cost guard"


async def test_a_safety_refusal_is_refused_so_the_fallback_can_take_it() -> None:
    body = message({}, stop="refusal", content=[])
    body["stop_details"] = {"category": "general_harms"}
    client, _ = live(reply(200, body))
    with pytest.raises(ModelRequestRefused) as caught:
        await client.invoke(request())
    assert str(caught.value) == "refusal", "the message names no category and no content"


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (429, "rate_limit_error"),
        (529, "overloaded_error"),
        (500, "api_error"),
        (502, "api_error"),
        (503, "api_error"),
    ],
)
async def test_throttling_overload_and_outages_are_service_busy_with_retry_after(status: int, kind: str) -> None:
    client, _ = live(reply(status, error_body(kind), **{"retry-after": "7"}))
    with pytest.raises(ServiceBusy) as caught:
        await client.invoke(request())
    assert caught.value.retry_after == 7.0


async def test_service_busy_without_a_retry_after_header_has_none() -> None:
    client, _ = live(reply(429, error_body("rate_limit_error")))
    with pytest.raises(ServiceBusy) as caught:
        await client.invoke(request())
    assert caught.value.retry_after is None


@pytest.mark.parametrize("error", [httpx.ReadTimeout("slow"), httpx.ConnectError("down")])
async def test_timeouts_and_dropped_connections_are_service_busy(error: Exception) -> None:
    def boom(_req: httpx.Request) -> httpx.Response:
        raise error

    client, _ = live(boom)
    with pytest.raises(ServiceBusy):
        await client.invoke(request())


async def test_a_spend_limit_is_unavailable_not_busy() -> None:
    client, _ = live(reply(429, error_body("rate_limit_error", "enforced_spend_limit_reached"), **{"retry-after": "7"}))
    with pytest.raises(ModelUnavailable):
        await client.invoke(request())


@pytest.mark.parametrize(
    ("status", "kind"),
    [(401, "authentication_error"), (403, "permission_error"), (404, "not_found_error")],
)
async def test_a_bad_key_or_missing_model_is_unavailable(status: int, kind: str) -> None:
    client, _ = live(reply(status, error_body(kind)))
    with pytest.raises(ModelUnavailable):
        await client.invoke(request())


@pytest.mark.parametrize("status", [400, 413, 422])
async def test_any_other_refusal_is_refused_and_names_no_body(status: int) -> None:
    client, _ = live(reply(status, error_body("invalid_request_error")))
    with pytest.raises(ModelRequestRefused) as caught:
        await client.invoke(request())
    assert "details the client must not log" not in str(caught.value)
