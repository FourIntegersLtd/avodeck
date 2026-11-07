"""A task's output schema in the form the direct API accepts for strict tool use.

With `strict: true` the API itself only lets the model answer in the schema (an `enum` is a closed list, an
extra key cannot be written), instead of the kit finding out afterwards. Strict mode takes a subset of JSON
Schema (https://platform.claude.com/docs/en/build-with-claude/structured-outputs):

- every object has `additionalProperties: false` (and the examples always list `required`);
- not supported: `minLength`/`maxLength`, `minimum`/`maximum`/`multipleOf`, array constraints beyond `minItems`
  0 or 1 (so `maxItems`), recursive schemas; `if`/`then` is not listed as supported;
- supported: `enum`, `const`, `anyOf`, `allOf` (without `$ref`), `$ref` to local definitions, simple `pattern`s,
  the common string `format`s.

`strict_schema` returns a copy (the task's schema is never changed, and the kit still validates every answer
against it afterwards: that is the backstop for lengths and ranges) in which

- every object is closed and requires all its properties (an optional property stays expressible as a nullable one);
- the unsupported constraints are dropped;
- a conditional shape `allOf: [{if: {kind: {const: K}}, then: {properties: ...}}]` becomes an `anyOf` with one
  branch per condition, the condition's property narrowed to that value.

Anthropic also limits strict schemas (20 strict tools, 24 optional properties, 16 union-typed properties, grammar
size). A schema over a limit is refused with a 400; `LiveAnthropic` then sends it once without strict.
Bedrock's Converse `toolSpec` has no strict flag, so Bedrock requests are not changed.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

# Keywords strict mode refuses. `minItems` of 0 or 1 is kept (handled below).
_DROPPED = frozenset(
    {
        "minLength",
        "maxLength",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "multipleOf",
        "maxItems",
        "uniqueItems",
        "minContains",
        "maxContains",
        "minProperties",
        "maxProperties",
    }
)
_MAPS = ("properties", "$defs", "definitions", "patternProperties")
_LISTS = ("anyOf", "allOf", "oneOf", "prefixItems")


def strict_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """`schema` in strict-mode form; see the module note. The given schema is not changed."""
    return _schema(copy.deepcopy(dict(schema)))


def _schema(node: dict[str, Any]) -> dict[str, Any]:
    node = _split_conditions(node)
    for key in _DROPPED:
        node.pop(key, None)
    if node.get("minItems", 0) > 1:
        del node["minItems"]
    for key in _MAPS:
        if isinstance(node.get(key), dict):
            node[key] = {name: _schema(sub) for name, sub in node[key].items() if isinstance(sub, dict)}
    for key in _LISTS:
        if isinstance(node.get(key), list):
            node[key] = [_schema(sub) if isinstance(sub, dict) else sub for sub in node[key]]
    if isinstance(node.get("items"), dict):
        node["items"] = _schema(node["items"])
    if node.get("type") == "object" or "properties" in node:
        node["additionalProperties"] = False
        node["required"] = list(node.get("properties", {}))
    return node


def _condition(entry: Any) -> tuple[str, Any] | None:
    """(property, value) for an `{if: {properties: {p: {const: v}}}, then: {...}}` entry, else None."""
    if not isinstance(entry, dict) or "if" not in entry or not isinstance(entry.get("then"), dict):
        return None
    if set(entry) - {"if", "then"}:
        return None
    props = entry["if"].get("properties") if isinstance(entry["if"], dict) else None
    if not isinstance(props, dict) or len(props) != 1:
        return None
    [(name, test)] = props.items()
    if not isinstance(test, dict) or set(test) != {"const"}:
        return None
    return name, test["const"]


def _split_conditions(node: dict[str, Any]) -> dict[str, Any]:
    """An object carrying `allOf` if/then entries on one property becomes an `anyOf` of one branch per value."""
    entries = node.get("allOf")
    if not isinstance(entries, list):
        return node
    conditions = [(entry, _condition(entry)) for entry in entries]
    found = [(entry, cond) for entry, cond in conditions if cond is not None]
    if not found or len({cond[0] for _, cond in found if cond}) != 1:
        return node
    name = found[0][1][0] if found[0][1] else ""
    base = {key: value for key, value in node.items() if key != "allOf"}
    rest = [entry for entry, cond in conditions if cond is None]
    branches: list[dict[str, Any]] = []
    covered: list[Any] = []
    for entry, cond in found:
        assert cond is not None
        value = cond[1]
        covered.append(value)
        branch = copy.deepcopy(base)
        props = branch.setdefault("properties", {})
        props[name] = {"enum": [value]}
        for other, sub in entry["then"].get("properties", {}).items():
            props[other] = {**props.get(other, {}), **sub}
        branches.append(branch)
    listed = base.get("properties", {}).get(name, {}).get("enum", [])
    left = [value for value in listed if value not in covered]
    if left:  # labels with no condition of their own keep the shape they had
        branch = copy.deepcopy(base)
        branch.setdefault("properties", {})[name] = {"enum": left}
        branches.append(branch)
    return {"anyOf": branches, **({"allOf": rest} if rest else {})}
