"""The direct request makes the API enforce the task's schema (strict tool use), so an off-list label or an
extra key cannot be answered at all (T-07b). The task's own schema is never changed: the kit still checks the
answer against it afterwards, which is the backstop for what strict mode cannot express (lengths, ranges).

The subset strict mode accepts (https://platform.claude.com/docs/en/build-with-claude/structured-outputs):
every object has `additionalProperties: false`; no `minLength`/`maxLength`, `minimum`/`maximum`,
`minItems` above 1, or `maxItems`; `anyOf` is supported, `if`/`then` is not mentioned."""

from __future__ import annotations

import copy
import json
from collections.abc import Iterator
from typing import Any

import httpx2
import pytest

from app.core.ai.anthropic_live import LiveAnthropic, _params
from app.core.ai.bedrock import ModelRequest
from app.core.ai.bedrock_live import _params as bedrock_params
from app.core.ai.strict import strict_schema
from app.core.packs import default_library
from app.features.pipeline import extract
from tests.support.ai import cell_env
from tests.support.live_ai import sse_events
from tests.support.packs import BOTH

pytestmark = pytest.mark.anyio

UNSUPPORTED = ("minLength", "maxLength", "minimum", "maximum", "maxItems", "multipleOf", "if", "then", "else")

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "kind": {"enum": ["a", "b"]},
        "reason": {"type": "string", "minLength": 1, "maxLength": 80},
        "score": {"type": "number", "minimum": 0, "maximum": 1},
        "ids": {"type": "array", "minItems": 1, "maxItems": 5, "items": {"type": "string", "maxLength": 40}},
        "rows": {
            "type": "array",
            "minItems": 2,
            "items": {"type": "object", "properties": {"n": {"type": "integer", "minimum": 1}}},
        },
        "either": {"anyOf": [{"type": "null"}, {"type": "object", "properties": {"x": {"type": "string"}}}]},
    },
}


def objects(node: Any) -> Iterator[dict[str, Any]]:
    """Every object-typed node of a schema, wherever it sits."""
    if isinstance(node, dict):
        if node.get("type") == "object" or "properties" in node:
            yield node
        for value in node.values():
            yield from objects(value)
    elif isinstance(node, list):
        for value in node:
            yield from objects(value)


def keywords(node: Any) -> Iterator[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            if key not in ("properties", "enum", "const"):  # property names and labels are not keywords
                yield key
            if key != "enum":
                yield from keywords(value)
    elif isinstance(node, list):
        for value in node:
            yield from keywords(value)


def assert_strict_shape(schema: dict[str, Any]) -> None:
    found = list(objects(schema))
    assert found
    for node in found:
        assert node.get("additionalProperties") is False, node
        assert node.get("required") == list(node.get("properties", {})), node


def test_every_object_is_closed_and_requires_all_its_properties_and_the_input_is_not_changed() -> None:
    before = copy.deepcopy(SCHEMA)
    strict = strict_schema(SCHEMA)
    assert SCHEMA == before
    assert_strict_shape(strict)
    assert strict["properties"]["kind"] == {"enum": ["a", "b"]}, "closed lists stay as they are"


def test_what_strict_mode_cannot_express_is_dropped_and_what_it_can_is_kept() -> None:
    strict = strict_schema(SCHEMA)
    assert not [k for k in keywords(strict) if k in UNSUPPORTED]
    assert strict["properties"]["ids"]["minItems"] == 1  # 0 and 1 are supported
    assert "minItems" not in strict["properties"]["rows"]  # 2 is not
    assert strict["properties"]["either"]["anyOf"][0] == {"type": "null"}


def test_a_conditional_shape_becomes_one_branch_per_condition() -> None:
    """`allOf: [{if kind == K, then fields: F_K}]` over a free-form `fields` is what T-07b uses."""
    fact = {
        "type": "object",
        "required": ["kind", "fields"],
        "properties": {"kind": {"enum": ["k1", "k2"]}, "fields": {"type": "object"}},
        "allOf": [
            {
                "if": {"properties": {"kind": {"const": key}}},
                "then": {"properties": {"fields": {"type": "object", "properties": {name: {"type": ["string", "null"]}}}}},
            }
            for key, name in (("k1", "date"), ("k2", "drug"))
        ],
    }
    strict = strict_schema({"type": "object", "properties": {"facts": {"type": "array", "items": fact}}})
    assert_strict_shape(strict)
    branches = strict["properties"]["facts"]["items"]["anyOf"]
    assert [b["properties"]["kind"] for b in branches] == [{"enum": ["k1"]}, {"enum": ["k2"]}]
    assert [list(b["properties"]["fields"]["properties"]) for b in branches] == [["date"], ["drug"]]
    assert not [k for k in keywords(strict) if k in ("allOf", "if", "then")]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_the_real_extraction_schema_is_made_strict_under_every_pack(combo: Any) -> None:
    pack = default_library().combine(*combo.codes)
    task_schema = extract.output_schema(pack)
    before = copy.deepcopy(task_schema)
    strict = strict_schema(task_schema)
    assert task_schema == before
    assert_strict_shape(strict)
    assert not [k for k in keywords(strict) if k in (*UNSUPPORTED, "allOf")]
    # the kind list survives, so an off-list kind cannot be answered
    kinds = {k["key"] for k in pack.fact_kinds()}
    seen = {e for node in objects(strict) for e in node.get("properties", {}).get("kind", {}).get("enum", [])}
    assert kinds <= seen


def request(schema: dict[str, Any]) -> ModelRequest:
    return ModelRequest(
        model_id="claude-sonnet-4-6", system="s", context={}, text="t", schema=schema, max_tokens=1024, task_id="T-x"
    )


def test_the_direct_request_asks_the_api_to_enforce_the_schema() -> None:
    sent = _params(request(SCHEMA))
    [tool] = sent["tools"]
    assert tool["strict"] is True
    assert_strict_shape(tool["input_schema"])
    assert sent["tool_choice"] == {"type": "tool", "name": "record_output"}


def test_the_bedrock_request_is_unchanged() -> None:
    """Converse `toolSpec` has no strict flag: the schema goes as the task wrote it."""
    sent = bedrock_params(request(SCHEMA))
    [tool] = sent["toolConfig"]["tools"]
    assert "strict" not in tool["toolSpec"]
    assert tool["toolSpec"]["inputSchema"] == {"json": SCHEMA}


def message() -> dict[str, Any]:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-4-6",
        "content": [{"type": "tool_use", "id": "t1", "name": "record_output", "input": {"kind": "a"}}],
        "stop_reason": "tool_use",
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


async def test_a_schema_the_api_rejects_is_sent_again_without_strict_and_remembered() -> None:
    """A schema strict mode refuses (too many union types, grammar too large) must not stop extraction: the
    post-hoc check stays the guard. The refusal is a 400 `invalid_request_error`, which names no content."""
    seen: list[dict[str, Any]] = []

    def handle(req: httpx2.Request) -> httpx2.Response:
        body = json.loads(req.content)
        seen.append(body)
        if body["tools"][0].get("strict"):
            return httpx2.Response(
                400, json={"type": "error", "error": {"type": "invalid_request_error", "message": "too complex"}}
            )
        return httpx2.Response(200, content=sse_events(message()), headers={"content-type": "text/event-stream"})

    from app.core.config import ai_settings

    settings = ai_settings(cell_env(AI_PRIMARY_PROVIDER="anthropic", ANTHROPIC_API_KEY="sk-ant-test-not-real"))
    client = LiveAnthropic(settings, "sk-ant-test-not-real", transport=httpx2.MockTransport(handle))
    first = await client.invoke(request(SCHEMA))
    assert first.output == {"kind": "a"}
    assert [bool(b["tools"][0].get("strict")) for b in seen] == [True, False]
    assert seen[1]["tools"][0]["input_schema"] == SCHEMA, "the task's own schema, as before"

    await client.invoke(request(SCHEMA))
    assert [bool(b["tools"][0].get("strict")) for b in seen] == [True, False, False], "not tried again"
