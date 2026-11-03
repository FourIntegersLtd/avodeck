"""A batch answer that leaves pages out is not the end of those pages: they are asked again, smaller,
so one page the model skips does not cost the pages beside it their extraction (T-07b)."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from app.features.pipeline.asking import answer_group

pytestmark = pytest.mark.anyio

Page = tuple[str, int]


def pages(*numbers: int) -> list[Page]:
    return [(f"id{n}", n) for n in numbers]


def never_refused(page: Page, answer: Mapping[str, Any]) -> bool:
    return False


class Script:
    """A model that answers every page it is asked about except those in `skips`; with `lone_ok`, a page asked
    on its own is always answered."""

    def __init__(self, skips: set[int], *, lone_ok: bool = False) -> None:
        self.skips = skips
        self.lone_ok = lone_ok
        self.calls: list[list[int]] = []

    async def __call__(self, group: Sequence[Page]) -> dict[str, Any] | None:
        self.calls.append([n for _, n in group])
        if len(group) == 1 and self.lone_ok:
            return {"pages": [{"page_no": group[0][1]}]}
        shown = [n for _, n in group if n not in self.skips]
        return {"pages": [{"page_no": n} for n in shown]} if shown else None


async def test_pages_left_out_of_a_batch_answer_are_asked_again_on_their_own() -> None:
    ask = Script({3, 7}, lone_ok=True)
    found = await answer_group(pages(*range(1, 11)), ask, never_refused)
    assert sorted(found) == list(range(1, 11))
    assert all(len(call) < 10 for call in ask.calls[2:])  # after the two whole-batch asks, only smaller groups


async def test_a_page_the_model_always_skips_does_not_cost_its_neighbours() -> None:
    ask = Script({4})
    found = await answer_group(pages(1, 2, 3, 4, 5), ask, never_refused)
    assert sorted(found) == [1, 2, 3, 5]


async def test_a_batch_with_no_answer_at_all_is_split_down_to_single_pages() -> None:
    calls: list[int] = []

    async def nothing(group: Sequence[Page]) -> None:
        calls.append(len(group))

    assert await answer_group(pages(1, 2, 3, 4), nothing, never_refused) == {}
    assert calls[0] == 4 and 1 in calls


async def test_a_complete_answer_is_asked_once() -> None:
    ask = Script(set())
    found = await answer_group(pages(1, 2, 3), ask, never_refused)
    assert sorted(found) == [1, 2, 3] and ask.calls == [[1, 2, 3]]


async def test_a_refused_page_has_the_batch_asked_a_second_time_and_no_more() -> None:
    ask = Script(set())

    def refused(page: Page, answer: Mapping[str, Any]) -> bool:
        return page[1] == 2

    found = await answer_group(pages(1, 2, 3), ask, refused)
    assert sorted(found) == [1, 2, 3] and len(ask.calls) == 2


async def test_pages_the_first_answer_gave_are_kept_when_the_second_whole_batch_answer_is_empty() -> None:
    whole: list[dict[str, Any] | None] = [{"pages": [{"page_no": 1}, {"page_no": 2}]}, None]
    calls: list[list[int]] = []

    async def ask(group: Sequence[Page]) -> dict[str, Any] | None:
        calls.append([n for _, n in group])
        if len(group) == 4:
            return whole.pop(0)
        return {"pages": [{"page_no": n} for _, n in group]}

    found = await answer_group(pages(1, 2, 3, 4), ask, never_refused)
    assert sorted(found) == [1, 2, 3, 4]
    assert calls == [[1, 2, 3, 4], [1, 2, 3, 4], [3, 4]], "the pages already answered are not asked again"
