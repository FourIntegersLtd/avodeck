"""Page-batch jobs (ADR 0038): cutting the pages that still need work into batches.

Pure functions over page numbers. Nothing here reads a page, a name or a date; the batches carry numbers only
(rule 6) and nothing country-specific (rule 11).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date
from uuid import UUID


@dataclass(frozen=True)
class BatchScope:
    """What a page-batch child works on: its run (the parent's job id), its batch, its page range (both ends
    included) and the bundle's latest printed date fixed when the run was planned (None when it had none)."""

    run_job_id: UUID
    batch_id: UUID
    first_page_no: int
    last_page_no: int
    date_anchor: date | None


@dataclass(frozen=True)
class PlannedBatch:
    """One batch of waiting pages: its place in the phase and the page-number range a child works on."""

    seq: int
    first_page_no: int
    last_page_no: int
    page_count: int


def worth_batching(pages: int, *, size: int, min_pages: int) -> bool:
    """True when a run is worth planning: the flag is on (`size` > 0) and enough pages still need work.

    `pages` is the union of pages without words and pages still to extract, so a bundle stopped during the sort
    is not read inline under the case lock."""
    return size > 0 and pages > 0 and pages >= min_pages


def plan(pages: Iterable[int], *, size: int, open_ranges: Sequence[tuple[int, int]] = ()) -> list[PlannedBatch]:
    """Cut `pages` into batches of `size` by page number.

    A page inside the range of an open run belongs to that run and is left out. A batch never straddles such a
    range, so the ranges of two open runs of a case never overlap. Ranges are in order and disjoint."""
    if size <= 0:
        return []
    waiting = sorted({n for n in pages if not any(first <= n <= last for first, last in open_ranges)})
    segments: list[list[int]] = []
    current: list[int] = []
    cuts = sorted(open_ranges)
    for n in waiting:
        if current and any(current[-1] < first <= n for first, _ in cuts):
            segments.append(current)
            current = []
        current.append(n)
    if current:
        segments.append(current)
    result: list[PlannedBatch] = []
    for segment in segments:
        for start in range(0, len(segment), size):
            chunk = segment[start : start + size]
            result.append(PlannedBatch(len(result), chunk[0], chunk[-1], len(chunk)))
    return result
