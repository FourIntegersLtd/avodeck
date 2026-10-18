"""Changed details (`facts.fields`) checked against the case pack's fact kinds (rule 11).

Each kind in the pack's `facts.yaml` names its fields and their types (string, integer, number, date,
boolean). A change may only name those fields, with values of those types; `null` clears a detail.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date
from typing import Any

_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _is_date(value: Any) -> bool:
    if not isinstance(value, str) or not _ISO_DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


_TYPES: dict[str, Callable[[Any], bool]] = {
    "string": lambda v: isinstance(v, str),
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, int | float) and not isinstance(v, bool),
    "boolean": lambda v: isinstance(v, bool),
    "date": _is_date,
}


def fits(kind_fields: Mapping[str, str] | None, fields: Mapping[str, Any]) -> bool:
    """Whether every sent field is one the kind has, with a value of its type. An unknown kind fits nothing."""
    if kind_fields is None:
        return not fields
    for key, value in fields.items():
        if key not in kind_fields:
            return False
        check = _TYPES.get(str(kind_fields[key]))
        if check is None or (value is not None and not check(value)):
            return False
    return True


def kinds(fact_kinds: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """The pack's fact kinds as {kind: {field: type}}."""
    return {str(k["key"]): {str(f): str(t) for f, t in (k.get("fields") or {}).items()} for k in fact_kinds}
