"""Recorded HTTP for the live AI clients: no test ever reaches Azure or AWS.

`AzureDi` is an `httpx.MockTransport` handler shaped like Azure AI Document Intelligence v4
(`2024-11-30`): a 202 with `Operation-Location` on submit, then `running` and `succeeded` polls.
`converse_response` is a Bedrock Converse reply carrying one forced tool call. Every value is made up.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx

from tests.support.ai import CELL_ENV

DI_ENDPOINT = CELL_ENV["AZURE_DI_ENDPOINT"]
DI_KEY = "test-di-key-not-real"
API_VERSION = "2024-11-30"
RESULT_ID = "3b6e0c3c-0000-4000-8000-000000000001"


def box(x: float, y: float, w: float = 0.5, h: float = 0.2) -> list[float]:
    return [x, y, x + w, y, x + w, y + h, x, y + h]


def di_page(number: int, words: Sequence[str], *, offset: int = 0) -> dict[str, Any]:
    out: list[dict[str, Any]] = []
    at = offset
    for i, text in enumerate(words):
        out.append(
            {
                "content": text,
                "polygon": box(1.0 + i * 0.6, 1.0),
                "confidence": 0.9 - i * 0.01,
                "span": {"offset": at, "length": len(text)},
            }
        )
        at += len(text) + 1
    return {
        "pageNumber": number,
        "angle": 0,
        "width": 8.5,
        "height": 11.0,
        "unit": "inch",
        "words": out,
        "lines": [],
        "spans": [{"offset": offset, "length": max(at - offset - 1, 0)}],
    }


def di_result(
    pages: Sequence[dict[str, Any]], *, model_id: str = "prebuilt-layout", handwritten: Iterable[dict[str, int]] = ()
) -> dict[str, Any]:
    spans = list(handwritten)
    return {
        "status": "succeeded",
        "createdDateTime": "2026-10-18T09:00:00Z",
        "lastUpdatedDateTime": "2026-10-18T09:00:02Z",
        "analyzeResult": {
            "apiVersion": API_VERSION,
            "modelId": model_id,
            "stringIndexType": "textElements",
            "content": "",
            "pages": list(pages),
            "styles": [{"confidence": 0.95, "spans": spans, "isHandwritten": True}] if spans else [],
        },
    }


def operation_url(model_id: str = "prebuilt-layout", base: str = DI_ENDPOINT) -> str:
    return f"{base}/documentintelligence/documentModels/{model_id}/analyzeResults/{RESULT_ID}?api-version={API_VERSION}"


@dataclass
class AzureDi:
    """Scripted Document Intelligence: `submits` and `polls` are replayed in order (last one repeats)."""

    polls: list[httpx.Response]
    submits: list[httpx.Response] = field(default_factory=list)
    operation: str | None = None
    requests: list[httpx.Request] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "POST":
            if self.submits:
                return self.submits.pop(0) if len(self.submits) > 1 else self.submits[0]
            model_id = request.url.path.rsplit("/", 1)[-1].split(":")[0]
            location = self.operation or operation_url(model_id)
            return httpx.Response(202, headers={"Operation-Location": location, "apim-request-id": "req-1"})
        return self.polls.pop(0) if len(self.polls) > 1 else self.polls[0]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)


def running() -> httpx.Response:
    return httpx.Response(200, json={"status": "running"}, headers={"Retry-After": "1"})


def succeeded(result: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json=result, headers={"apim-request-id": "req-2"})


class Sleeps:
    def __init__(self) -> None:
        self.waits: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def converse_response(output: dict[str, Any], *, input_tokens: int = 120, output_tokens: int = 30) -> dict[str, Any]:
    return {
        "output": {
            "message": {
                "role": "assistant",
                "content": [{"toolUse": {"toolUseId": "tooluse_1", "name": "record_output", "input": output}}],
            }
        },
        "stopReason": "tool_use",
        "usage": {
            "inputTokens": input_tokens,
            "outputTokens": output_tokens,
            "totalTokens": input_tokens + output_tokens,
        },
        "metrics": {"latencyMs": 812},
        "ResponseMetadata": {"RequestId": "aws-req-1", "HTTPStatusCode": 200, "HTTPHeaders": {}, "RetryAttempts": 0},
    }


AWS_KEYS = {"AWS_ACCESS_KEY_ID": "AKIATESTNOTREAL00000", "AWS_SECRET_ACCESS_KEY": "test-secret-not-real"}


def sse_events(message: dict[str, Any], *, fail_with: dict[str, Any] | None = None) -> bytes:
    """A Messages API message as the server-sent events of a streamed reply.

    `fail_with` ends the stream with an `error` event (after the status was already 200) instead of finishing.
    The tool input (or text) goes out in small `partial_json` / `text_delta` pieces, as the API sends it.
    """
    import json as _json

    def event(name: str, data: dict[str, Any]) -> str:
        return f"event: {name}\ndata: {_json.dumps(data)}\n\n"

    usage = dict(message["usage"])
    out = [
        event(
            "message_start",
            {
                "type": "message_start",
                "message": {
                    **{k: v for k, v in message.items() if k not in ("content", "stop_reason")},
                    "content": [],
                    "stop_reason": None,
                    "usage": {**usage, "output_tokens": 1},
                },
            },
        )
    ]
    for index, block in enumerate(message["content"]):
        if block["type"] == "tool_use":
            out.append(
                event(
                    "content_block_start",
                    {"type": "content_block_start", "index": index, "content_block": {**block, "input": {}}},
                )
            )
            raw = _json.dumps(block["input"])
            for at in range(0, len(raw), 40):
                out.append(
                    event(
                        "content_block_delta",
                        {
                            "type": "content_block_delta",
                            "index": index,
                            "delta": {"type": "input_json_delta", "partial_json": raw[at : at + 40]},
                        },
                    )
                )
        else:
            out.append(
                event(
                    "content_block_start",
                    {"type": "content_block_start", "index": index, "content_block": {"type": "text", "text": ""}},
                )
            )
            out.append(
                event(
                    "content_block_delta",
                    {
                        "type": "content_block_delta",
                        "index": index,
                        "delta": {"type": "text_delta", "text": block["text"]},
                    },
                )
            )
        if fail_with is not None and index == 0:
            out.append(event("error", fail_with))
            return "".join(out).encode()
        out.append(event("content_block_stop", {"type": "content_block_stop", "index": index}))
    if fail_with is not None:
        out.append(event("error", fail_with))
        return "".join(out).encode()
    out.append(
        event(
            "message_delta",
            {
                "type": "message_delta",
                "delta": {"stop_reason": message["stop_reason"], "stop_sequence": None},
                "usage": {"output_tokens": usage["output_tokens"]},
            },
        )
    )
    out.append(event("message_stop", {"type": "message_stop"}))
    return "".join(out).encode()
