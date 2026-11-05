"""Bundle search: the case's decrypted words, kept for a short time (scalability PR 10).

Opening every word of a 4,000-page bundle from its seal is the cost of a search, not the matching. So the
opened words are kept in this process, per case:

- at most `READER_SEARCH_CACHE_CASES` cases (default 8, least recently used out) and `READER_SEARCH_CACHE_MAX_WORDS`
  words in all (default 3 million; a case over the cap alone is answered but not kept): the memory bound;
- for `READER_SEARCH_CACHE_TTL_SECONDS` (default 10 minutes);
- until the caller's `version` changes (pages added, set aside or read again, the expert's readings changed),
  because another process or the worker may have changed the words; the caller reads the version from the
  database on every search;
- until `clear_case` is called (the case key was destroyed, PR 15) or `clear_all`.

What is held is patient text. It is never logged, never returned as words, and the keys are case ids only
(rule 6). Access is checked by the route before the cache is asked (rule 9); the cache does not know people.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from array import array
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from app.core.config import reader_search_cache_settings

type Version = tuple[object, ...]


@dataclass(slots=True)
class PageWords:
    """One page's words in reading order: ids, lower-case text, and 8 polygon numbers per word (flat)."""

    ids: list[str]
    texts: list[str]
    poly: array  # type: ignore[type-arg]  # 'f', 8 numbers per word


@dataclass(slots=True)
class CaseWords:
    """The words of a case's pages that are not set aside, by page id, and each page's size where known."""

    pages: dict[uuid.UUID, PageWords]
    sizes: dict[uuid.UUID, tuple[float, float]] = field(default_factory=dict)

    @property
    def word_count(self) -> int:
        return sum(len(p.ids) for p in self.pages.values())


@dataclass(slots=True)
class _Entry:
    words: CaseWords
    version: Version
    stored_at: float
    size: int


class SearchCache:
    def __init__(
        self, *, max_cases: int, ttl_seconds: float, max_words: int, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.max_cases = max_cases
        self.ttl_seconds = ttl_seconds
        self.max_words = max_words
        self._clock = clock
        self._entries: OrderedDict[uuid.UUID, _Entry] = OrderedDict()
        self._locks: dict[uuid.UUID, asyncio.Lock] = {}
        self._generation: dict[uuid.UUID, int] = {}
        self._epoch = 0  # bumped by clear_all
        self.word_count = 0

    def __len__(self) -> int:
        return len(self._entries)

    def case_ids(self) -> list[uuid.UUID]:
        return list(self._entries)

    async def get_words(
        self, case_id: uuid.UUID, loader: Callable[[], Awaitable[CaseWords]], *, version: Version
    ) -> CaseWords:
        """The case's words: from memory while fresh and unchanged, else from `loader` (once, however many ask)."""
        hit = self._fresh(case_id, version)
        if hit is not None:
            return hit
        lock = self._locks.setdefault(case_id, asyncio.Lock())
        async with lock:
            hit = self._fresh(case_id, version)
            if hit is not None:
                return hit
            stamp = (self._epoch, self._generation.get(case_id, 0))
            words = await loader()
            if stamp == (self._epoch, self._generation.get(case_id, 0)):  # not cleared while it was loading
                self._store(case_id, words, version)
            self._forget_idle_locks()
            return words

    def clear_case(self, case_id: uuid.UUID) -> None:
        """Forget the case's words (its key was destroyed); a load running now is not kept."""
        self._generation[case_id] = self._generation.get(case_id, 0) + 1
        self._drop(case_id)

    def clear_all(self) -> None:
        self._epoch += 1
        self._entries.clear()
        self.word_count = 0

    def _forget_idle_locks(self) -> None:
        if len(self._locks) > 4 * self.max_cases:
            self._locks = {k: v for k, v in self._locks.items() if v.locked()}

    def _fresh(self, case_id: uuid.UUID, version: Version) -> CaseWords | None:
        entry = self._entries.get(case_id)
        if entry is None:
            return None
        if entry.version != version or self._clock() - entry.stored_at >= self.ttl_seconds:
            self._drop(case_id)
            return None
        self._entries.move_to_end(case_id)
        return entry.words

    def _drop(self, case_id: uuid.UUID) -> None:
        entry = self._entries.pop(case_id, None)
        if entry is not None:
            self.word_count -= entry.size

    def _store(self, case_id: uuid.UUID, words: CaseWords, version: Version) -> None:
        self._drop(case_id)
        size = words.word_count
        if size > self.max_words:
            return
        self._entries[case_id] = _Entry(words, version, self._clock(), size)
        self.word_count += size
        while len(self._entries) > self.max_cases or self.word_count > self.max_words:
            oldest = next(iter(self._entries))
            self._drop(oldest)


def _build() -> SearchCache:
    s = reader_search_cache_settings()
    return SearchCache(max_cases=s.cases, ttl_seconds=s.ttl_seconds, max_words=s.max_words)


_cache = _build()


async def get_words(case_id: uuid.UUID, loader: Callable[[], Awaitable[CaseWords]], *, version: Version) -> CaseWords:
    return await _cache.get_words(case_id, loader, version=version)


def clear_case(case_id: uuid.UUID) -> None:
    """Drop one case's words. Called when the case key is destroyed (PR 15)."""
    _cache.clear_case(case_id)


def clear_all() -> None:
    _cache.clear_all()


def case_ids() -> list[uuid.UUID]:
    return _cache.case_ids()
