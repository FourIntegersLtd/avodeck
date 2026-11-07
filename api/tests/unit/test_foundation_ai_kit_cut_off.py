"""When is an answer cut off by the output cap rather than wrong? (T-07b, direct provider.)

An answer that used the whole cap is cut off. So is one that ended within 256 tokens of it, and one in the last
tenth of the cap that fails at its tail (an extra key or a missing field: the shape of a JSON cut off
mid-way). A clean off-list label at ordinary size is a wrong answer, asked again once."""

from __future__ import annotations

from typing import Any

import jsonschema_rs
import pytest

from app.core.ai.kit import looks_cut_off

SCHEMA = {
    "type": "object",
    "properties": {"kind": {"enum": ["a", "b"]}, "n": {"type": "integer"}},
    "required": ["kind", "n"],
    "additionalProperties": False,
}
VALIDATOR = jsonschema_rs.validator_for(SCHEMA)
CAP = 32000


@pytest.mark.parametrize(
    ("output", "tokens", "expected"),
    [
        ({}, CAP, True),  # the whole cap
        ({"kind": "a", "n": 1}, CAP, True),  # whole cap even when the JSON happens to be valid in shape
        ({}, CAP - 256, True),  # the margin
        ({}, CAP - 257, True),  # not within the margin, but at the tail with a missing field
        ({"kind": "a", "n": 1, "x": 2}, 31934, True),  # production: 66 short of the cap, AdditionalProperties
        ({"kind": "z", "n": 1}, 10525, False),  # a clean Enum error at ordinary size: a wrong answer
        ({"kind": "z", "n": 1}, 31000, False),  # an Enum error is wrong even near the cap: JSON complete
        ({}, 28000, False),  # under 0.9 of the cap: a wrong answer
        ({}, 28800, True),  # at 0.9 of the cap with a missing field
        ({"kind": "a", "n": 1, "x": 2}, 28799, False),
    ],
)
def test_cut_off_or_wrong(output: dict[str, Any], tokens: int, expected: bool) -> None:
    assert looks_cut_off(tokens, CAP, output, VALIDATOR) is expected


def test_a_small_cap_keeps_the_old_rule_only() -> None:
    """Tests and small tasks use caps of a few hundred tokens: a margin of 256 would be half the answer."""
    assert looks_cut_off(500, 500, {}, VALIDATOR) is True
    assert looks_cut_off(499, 500, {}, VALIDATOR) is False
    assert looks_cut_off(4000, 4096, {}, VALIDATOR) is False
