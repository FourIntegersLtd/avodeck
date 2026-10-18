"""Timeline lanes from the pack (`pack.lanes()`): which lane a fact goes in.

A lane is `{key, kinds?, when?}`. A lane whose `when` matches the fact's fields wins; otherwise the
first lane listing the fact's kind without a condition. A plain string lane lists no kinds. Facts
no lane takes are returned apart, never dropped.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

Lane = Mapping[str, Any] | str


def _lane(lane: Lane) -> Mapping[str, Any]:
    return {"key": lane} if isinstance(lane, str) else lane


def lane_for(kind: str, fields: Mapping[str, Any], lanes: Sequence[Lane]) -> str | None:
    plain: str | None = None
    for raw in lanes:
        lane = _lane(raw)
        if kind not in lane.get("kinds", ()):
            continue
        when = lane.get("when")
        if when:
            if all(fields.get(field) == value for field, value in when.items()):
                return str(lane["key"])
        elif plain is None:
            plain = str(lane["key"])
    return plain


def arrange[T](
    facts: Iterable[T],
    lanes: Sequence[Lane],
    *,
    kind: Callable[[T], str],
    fields: Callable[[T], Mapping[str, Any]],
) -> tuple[list[tuple[str, list[T]]], list[T]]:
    keyed: dict[str, list[T]] = {str(_lane(lane)["key"]): [] for lane in lanes}
    unplaced: list[T] = []
    for fact in facts:
        key = lane_for(kind(fact), fields(fact), lanes)
        (keyed[key] if key is not None else unplaced).append(fact)
    return list(keyed.items()), unplaced
