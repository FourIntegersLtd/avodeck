"""Asking the model about a group of pages (T-07b), and what to do when the answer leaves pages out.

A page is only marked as having no usable answer once it has been asked about on its own: a batch
answer that is empty is split in half and each half asked again, and a batch answer that skips some
pages has just those pages asked again. One page the model cannot answer therefore costs only itself.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any

from app.features.pipeline import extract

Page = tuple[Any, int]  # (page id, bundle page number)


async def answer_group(
    group: Sequence[Page],
    ask: Callable[[Sequence[Page]], Awaitable[Mapping[str, Any] | None]],
    refused: Callable[[Page, Mapping[str, Any]], bool],
) -> dict[int, Mapping[str, Any]]:
    """The answers for the group's pages, by page number.

    The group is asked twice at most when a page is missing or `refused` (failed provenance); the second
    answer stands unless it is empty, when the first one's pages are kept. A group with no answer at all is
    split in half and each half asked again, down to single pages. Pages the answer left out are then asked
    again on their own, as a smaller group.
    """
    found: dict[int, Mapping[str, Any]] = {}
    for _ in range(2):
        raw = await ask(group)
        got = extract.pages_of(raw) if raw is not None else {}
        if not got and found:
            break  # an empty second answer does not undo the pages the first one gave
        found = got
        if not found and len(group) > 1:
            half = len(group) // 2
            return {**await answer_group(group[:half], ask, refused), **await answer_group(group[half:], ask, refused)}
        if raw is not None and all(page[1] in found and not refused(page, found[page[1]]) for page in group):
            break
    left_out = [page for page in group if page[1] not in found]
    if left_out and len(group) > 1:
        found = {**found, **await answer_group(left_out, ask, refused)}
    return found
