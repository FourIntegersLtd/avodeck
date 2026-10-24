"""Rule 6 for the support console: no support answer can carry free text (support plan; permissions.md "Avodeck staff").

Every string in every response of a `/v1/support/...` route must be an enum, an id, a date or time, or a
code held to a pattern. A plain `str` field could one day carry a name, page text, an identifier or a date
of birth to Avodeck staff, so it fails here before it can.
"""

from __future__ import annotations

from typing import Any

from app.main import create_app

SAFE_FORMATS = {"uuid", "date-time", "date"}


def _resolve(spec: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    while "$ref" in node:
        node = spec["components"]["schemas"][node["$ref"].rsplit("/", 1)[-1]]
    return node


def _free_strings(spec: dict[str, Any], node: dict[str, Any], where: str, seen: set[str]) -> list[str]:
    if "$ref" in node:
        name = node["$ref"].rsplit("/", 1)[-1]
        if name in seen:
            return []
        seen.add(name)
        return _free_strings(spec, _resolve(spec, node), name, seen)
    found: list[str] = []
    for key in ("anyOf", "oneOf", "allOf"):
        for part in node.get(key, []):
            found += _free_strings(spec, part, where, seen)
    if node.get("type") == "string" and not (
        "enum" in node or "const" in node or node.get("format") in SAFE_FORMATS or "pattern" in node
    ):
        found.append(where)
    if "items" in node:
        found += _free_strings(spec, node["items"], f"{where}[]", seen)
    for prop, sub in node.get("properties", {}).items():
        found += _free_strings(spec, sub, f"{where}.{prop}", seen)
    extra = node.get("additionalProperties")
    if isinstance(extra, dict):
        found += _free_strings(spec, extra, f"{where}{{}}", seen)
    return found


def test_support_responses_carry_only_enums_ids_dates_and_codes() -> None:
    spec = create_app().openapi()
    support = {p: ops for p, ops in spec["paths"].items() if p.startswith("/v1/support/")}
    assert "/v1/support/cases/{case_id}" in support
    free: list[str] = []
    for path, ops in support.items():
        for method, op in ops.items():
            for status, response in op.get("responses", {}).items():
                if not status.startswith("2"):
                    continue
                for media in response.get("content", {}).values():
                    free += [f"{method.upper()} {path}: {w}" for w in _free_strings(spec, media["schema"], "", set())]
    assert free == [], f"free text in a support answer (rule 6); use an enum, id, date or pattern code: {free}"
