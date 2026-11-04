"""The cost guard's numbers and formula are written once (F11).

The API says a case has "used up its allowance" from `cost_guard()`; the worker's AI kit trips the guard from
`ai_settings()`. Both must come from the one `CostGuard`, so the two cannot drift: the same three variables
(`AI_COST_GUARD_MULTIPLE`, `AI_EXPECTED_TOKENS_PER_PAGE`, `AI_MIN_EXPECTED_PAGES`) set the same allowance on
both, and the kit asks `CostGuard.allowance` rather than working the sum out itself.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.ai.kit import AiKit
from app.core.config import CostGuard, ai_settings, cost_guard
from app.core.errors import ProcessingPaused
from tests.support.ai import ScriptedBedrock, cell_env

pytestmark = pytest.mark.anyio

TUNED = {"AI_COST_GUARD_MULTIPLE": "5", "AI_EXPECTED_TOKENS_PER_PAGE": "2500", "AI_MIN_EXPECTED_PAGES": "40"}


class _Cursor:
    def __init__(self, row: tuple[int, int]) -> None:
        self.row = row

    async def fetchone(self) -> tuple[int, int]:
        return self.row


class _Budget:
    """A connection whose `app.ai_budget(case)` says `used` tokens over `pages` pages."""

    def __init__(self, used: int, pages: int) -> None:
        self.row = (used, pages)

    async def execute(self, _sql: str, _params: tuple[object, ...]) -> _Cursor:
        return _Cursor(self.row)


def kit(**env: str) -> Any:
    return AiKit(ai_settings(cell_env(**env)), ScriptedBedrock([]))


def test_the_ai_settings_carry_the_same_guard_as_cost_guard() -> None:
    env = cell_env(**TUNED)
    settings: Any = ai_settings(env)
    assert (
        settings.cost_guard
        == cost_guard(env)
        == CostGuard(multiple=5, expected_tokens_per_page=2500, min_expected_pages=40)
    )


def test_with_nothing_set_both_use_the_one_set_of_defaults() -> None:
    settings: Any = ai_settings(cell_env())
    assert settings.cost_guard == cost_guard({}) == CostGuard()


def test_the_ai_settings_refuse_a_guard_number_cost_guard_refuses() -> None:
    from app.core.config import ConfigError

    for name in TUNED:
        with pytest.raises(ConfigError):
            ai_settings(cell_env(**{name: "0"}))
        with pytest.raises(ConfigError):
            cost_guard({name: "0"})


async def test_the_kit_trips_the_guard_where_the_cost_guard_allowance_says() -> None:
    k = kit(**TUNED)
    allowance = cost_guard(cell_env(**TUNED)).allowance(300)
    case = uuid.uuid4()
    await k._cost_guard(_Budget(allowance - 1, 300), case)
    with pytest.raises(ProcessingPaused) as caught:
        await k._cost_guard(_Budget(allowance, 300), case)
    assert caught.value.reason == "cost_guard"


async def test_the_kit_asks_the_cost_guard_for_the_allowance_rather_than_working_it_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(CostGuard, "allowance", lambda _self, _pages: 1)
    with pytest.raises(ProcessingPaused):
        await kit()._cost_guard(_Budget(1, 10), uuid.uuid4())
