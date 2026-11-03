"""A batch whose answer is cut off at the output cap is split until it fits, never asked again whole; a page
that is cut off even on its own costs only itself (T-07b)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.core.ai.kit import OutputTruncated
from app.features.pipeline.asking import answer_group

pytestmark = pytest.mark.anyio

Page = tuple[str, int]


def pages(*numbers: int) -> list[Page]:
    return [(f"id{n}", n) for n in numbers]


def never_refused(page: Page, answer: Mapping[str, Any]) -> bool:
    return False


async def test_a_batch_whose_answer_is_cut_off_is_split_until_it_fits() -> None:
    calls: list[list[int]] = []

    async def ask(group: Sequence[Page]) -> dict[str, Any]:
        calls.append([n for _, n in group])
        if len(group) > 2:  # the cap holds two pages' answers, not three or more
            raise OutputTruncated("T-test")
        return {"pages": [{"page_no": n} for _, n in group]}

    found = await answer_group(pages(*range(1, 8)), ask, never_refused)
    assert sorted(found) == list(range(1, 8))
    assert calls[0] == list(range(1, 8))
    assert all(calls.count(call) == 1 for call in calls)  # a cut-off group is never asked again whole


async def test_a_lone_page_cut_off_at_every_size_costs_only_itself() -> None:
    async def ask(group: Sequence[Page]) -> dict[str, Any]:
        if any(n == 3 for _, n in group):
            raise OutputTruncated("T-test")
        return {"pages": [{"page_no": n} for _, n in group]}

    found = await answer_group(pages(1, 2, 3, 4), ask, never_refused)
    assert sorted(found) == [1, 2, 4]
