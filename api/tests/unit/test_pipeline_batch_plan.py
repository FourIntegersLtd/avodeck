"""Cutting the pages that still need work into page batches (ADR 0038, plan step).

The plan is pure: page numbers in, ranges out. Counts and numbers only, no page text (rule 6).
"""

from __future__ import annotations

from app.features.pipeline import batches
from app.features.pipeline.batches import PlannedBatch


def test_900_waiting_pages_make_nine_batches_of_100_in_number_order() -> None:
    plan = batches.plan(list(range(1, 901)), size=100)
    assert [(b.seq, b.first_page_no, b.last_page_no, b.page_count) for b in plan] == [
        (i, i * 100 + 1, i * 100 + 100, 100) for i in range(9)
    ]


def test_unsorted_input_is_cut_by_page_number() -> None:
    plan = batches.plan([5, 3, 4, 1, 2], size=2)
    assert plan == [PlannedBatch(0, 1, 2, 2), PlannedBatch(1, 3, 4, 2), PlannedBatch(2, 5, 5, 1)]


def test_gaps_and_dropped_copies_still_give_full_batches_of_waiting_pages() -> None:
    waiting = [n for n in range(1, 401) if n % 4 != 0]  # every fourth page already has words
    plan = batches.plan(waiting, size=100)
    assert [b.page_count for b in plan] == [100, 100, 100]
    assert plan[0].first_page_no == 1 and plan[0].last_page_no == 133


def test_a_page_given_twice_is_counted_once() -> None:
    assert batches.plan([1, 1, 2, 2, 3], size=10) == [PlannedBatch(0, 1, 3, 3)]


def test_ranges_are_in_order_disjoint_and_cover_every_waiting_page_once() -> None:
    waiting = sorted({n * 3 % 997 + 1 for n in range(700)})
    plan = batches.plan(waiting, size=64)
    seen: list[int] = []
    for earlier, later in zip(plan, plan[1:], strict=False):
        assert earlier.last_page_no < later.first_page_no
    for b in plan:
        inside = [n for n in waiting if b.first_page_no <= n <= b.last_page_no]
        assert len(inside) == b.page_count
        seen += inside
    assert seen == waiting
    assert [b.seq for b in plan] == list(range(len(plan)))


def test_pages_inside_an_open_runs_range_are_left_out_and_no_batch_straddles_it() -> None:
    plan = batches.plan(list(range(1, 301)), size=100, open_ranges=[(101, 200)])
    assert [(b.first_page_no, b.last_page_no, b.page_count) for b in plan] == [(1, 100, 100), (201, 300, 100)]
    straddle = batches.plan(list(range(1, 251)), size=100, open_ranges=[(51, 150)])
    for b in straddle:
        assert b.last_page_no < 51 or b.first_page_no > 150
    assert sum(b.page_count for b in straddle) == 50 + 100


def test_nothing_waiting_or_size_zero_gives_no_batches() -> None:
    assert batches.plan([], size=100) == []
    assert batches.plan([1, 2, 3], size=0) == []


def test_worth_batching_needs_the_flag_on_and_enough_pages() -> None:
    assert batches.worth_batching(250, size=100, min_pages=100)
    assert batches.worth_batching(100, size=100, min_pages=100)
    assert not batches.worth_batching(99, size=100, min_pages=100)
    assert not batches.worth_batching(5000, size=0, min_pages=0)  # flag off: today's single job
    assert not batches.worth_batching(0, size=100, min_pages=0)
