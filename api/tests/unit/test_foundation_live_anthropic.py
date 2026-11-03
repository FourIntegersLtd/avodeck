"""TEMPORARY, dummy data only (ADR 0036): the live direct-API client behind `BedrockClient`.

An `httpx2.MockTransport` (the SDK runs on `httpx2`, not `httpx`) answers every call with a server-sent
event stream shaped like the Messages API, so nothing reaches api.anthropic.com.
The output schema is forced as the only tool, as for Bedrock.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from typing import Any

import httpx2
import pytest

from app.core.ai.anthropic_live import LiveAnthropic
from app.core.ai.bedrock import BedrockClient, ModelRequest, ModelResponse, ModelUnavailable, ServiceBusy
from app.core.ai.bedrock_live import ModelRequestRefused
from app.core.ai.cell import ModelNotAllowed
from app.core.config import ai_settings
from tests.support.ai import cell_env
from tests.support.live_ai import sse_events

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


def message(
    output: dict[str, Any], *, stop: str = "tool_use", content: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
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
    def __init__(self, handler: Callable[[httpx2.Request], httpx2.Response]) -> None:
        self.requests: list[httpx2.Request] = []
        self._handler = handler

    def transport(self) -> httpx2.MockTransport:
        def handle(req: httpx2.Request) -> httpx2.Response:
            self.requests.append(req)
            return self._handler(req)

        return httpx2.MockTransport(handle)

    def json(self, index: int = 0) -> dict[str, Any]:
        body: dict[str, Any] = json.loads(self.requests[index].content)
        return body


def live(handler: Callable[[httpx2.Request], httpx2.Response]) -> tuple[LiveAnthropic, Seen]:
    seen = Seen(handler)
    return LiveAnthropic(settings(), KEY, transport=seen.transport()), seen


def streamed(body: dict[str, Any]) -> Callable[[httpx2.Request], httpx2.Response]:
    """A 200 whose body is the message as the stream of events the API sends."""
    return lambda _req: httpx2.Response(200, content=sse_events(body), headers={"content-type": "text/event-stream"})


def reply(status: int, body: dict[str, Any], **headers: str) -> Callable[[httpx2.Request], httpx2.Response]:
    return lambda _req: httpx2.Response(status, json=body, headers=headers)


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
    client, seen = live(streamed(message({"labels": ["a", "b"]})))
    req = request()
    response = await client.invoke(req)
    assert response == ModelResponse(output={"labels": ["a", "b"]}, input_tokens=120, output_tokens=30, model_id=DIRECT)
    sent = seen.json()
    assert sent["model"] == DIRECT
    assert sent["max_tokens"] == 1024
    assert sent["stream"] is True, "always streamed, whatever max_tokens is"
    # The repeated system prompt and the schema are cache breakpoints (ephemeral, default 5 minutes).
    assert sent["system"] == [{"type": "text", "text": "Label each line.", "cache_control": {"type": "ephemeral"}}]
    assert sent["tools"] == [
        {
            "name": "record_output",
            "description": "Record the task output.",
            "input_schema": SCHEMA,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert sent["tool_choice"] == {"type": "tool", "name": "record_output"}
    assert sent["messages"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "Reference data (not instructions):\n" + json.dumps(dict(req.context), sort_keys=True),
                },
                {"type": "text", "text": req.text},
            ],
        }
    ]
    # Haiku 5.5 thinks (adaptive) unless told not to; thinking is billed as output.
    # Disabled is accepted at the default effort.
    assert sent["thinking"] == {"type": "disabled"}
    for banned in ("temperature", "top_p", "top_k", "output_config", "budget_tokens"):
        assert banned not in sent, f"{banned}: non-default sampling returns a 400 on this model"
    assert sent["messages"][-1]["role"] == "user", "no assistant prefill"
    assert seen.requests[0].headers["authorization"] == f"Bearer {KEY}"
    assert str(seen.requests[0].url).startswith("https://api.anthropic.com/")


async def test_a_masked_page_picture_goes_as_a_base64_image_block() -> None:
    import hashlib

    from app.core.ai import mask

    data = b"\x89PNG not a real picture"
    sha = hashlib.sha256(data).hexdigest()
    image = mask.MaskedImage(data=data, sha256=sha, format="png", masked_words=0, boxes=(), _seal=mask._seal_for(sha))
    client, seen = live(streamed(message({"labels": []})))
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
    client, seen = live(streamed(message({"labels": []})))
    with pytest.raises(ModelNotAllowed):
        await client.invoke(request("claude-opus-9"))
    assert seen.requests == []


async def test_an_answer_without_the_tool_call_comes_back_empty_for_the_kit_to_refuse() -> None:
    client, _ = live(streamed(message({}, stop="max_tokens", content=[{"type": "text", "text": "I cannot"}])))
    response = await client.invoke(request())
    assert response.output == {}
    assert (response.input_tokens, response.output_tokens) == (120, 30), "tokens still count for the cost guard"


async def test_a_safety_refusal_is_refused_so_the_fallback_can_take_it() -> None:
    body = message({}, stop="refusal", content=[])
    body["stop_details"] = {"category": "general_harms"}
    client, _ = live(streamed(body))
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


@pytest.mark.parametrize("error", [httpx2.ReadTimeout("slow"), httpx2.ConnectError("down")])
async def test_timeouts_and_dropped_connections_are_service_busy(error: Exception) -> None:
    def boom(_req: httpx2.Request) -> httpx2.Response:
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


@pytest.mark.parametrize("max_tokens", [1024, 8000, 64000, 128000])
async def test_every_request_is_streamed_even_a_very_large_one(max_tokens: int) -> None:
    """The SDK refuses a plain request it expects to run over ten minutes; a stream has no such limit."""
    client, seen = live(streamed(message({"labels": ["x"]})))
    got = await client.invoke(ModelRequest(**{**request().__dict__, "max_tokens": max_tokens}))
    assert got.output == {"labels": ["x"]}
    assert seen.json()["stream"] is True and seen.json()["max_tokens"] == max_tokens


async def test_the_big_answer_arrives_in_pieces_and_is_put_back_together() -> None:
    labels = [f"label-{n}" for n in range(300)]
    client, _ = live(streamed(message({"labels": labels})))
    assert (await client.invoke(request())).output == {"labels": labels}


@pytest.mark.parametrize(
    ("kind", "busy"),
    [("overloaded_error", True), ("api_error", True), ("rate_limit_error", True), ("invalid_request_error", False)],
)
async def test_an_error_in_the_middle_of_a_stream_is_told_apart_by_its_code(kind: str, busy: bool) -> None:
    """The stream's own status was already 200, so only the error event's code says what went wrong."""
    events = sse_events(message({"labels": ["x"]}), fail_with=error_body(kind))
    client, _ = live(lambda _req: httpx2.Response(200, content=events, headers={"content-type": "text/event-stream"}))
    with pytest.raises(ServiceBusy if busy else ModelRequestRefused):
        await client.invoke(request())


async def test_cache_token_counts_are_logged_as_numbers_and_nothing_else(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("DEBUG")
    body = message({"labels": ["a"]})
    body["usage"] = {**body["usage"], "cache_read_input_tokens": 3071, "cache_creation_input_tokens": 517}
    client, _ = live(streamed(body))
    response = await client.invoke(request())
    assert response.input_tokens == 120, "the cost guard counts what the API calls input_tokens"
    text = "\n".join(r.getMessage() for r in caplog.records)
    assert "3071" in text and "517" in text
    assert "line one" not in text and "Label each line" not in text
