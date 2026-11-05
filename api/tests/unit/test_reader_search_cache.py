"""The bundle-search cache (scalability PR 10): decrypted words kept per case, bounded and short-lived.

Keys are case ids only (rule 6); the words held are patient text and never leave the process.
"""

from __future__ import annotations

import asyncio
import uuid
from array import array

import pytest

from app.core.config import ConfigError, reader_search_cache_settings
from app.features.reader import search_cache
from app.features.reader.search_cache import CaseWords, PageWords, SearchCache

pytestmark = pytest.mark.anyio


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def words(count: int = 3) -> CaseWords:
    """One page of `count` words."""
    page = PageWords(
        ids=[f"w{i}" for i in range(count)],
        texts=[f"word{i}" for i in range(count)],
        poly=array("f", [0.0, 0.0, 1.0, 0.0, 1.0, 1.0, 0.0, 1.0] * count),
    )
    return CaseWords(pages={uuid.uuid4(): page}, sizes={})


class Loader:
    def __init__(self, count: int = 3) -> None:
        self.calls = 0
        self.count = count

    async def __call__(self) -> CaseWords:
        self.calls += 1
        await asyncio.sleep(0)
        return words(self.count)


def cache(clock: Clock | None = None, **kw: int | float) -> SearchCache:
    settings = {"max_cases": 8, "ttl_seconds": 600.0, "max_words": 1_000_000, **kw}
    return SearchCache(clock=clock or Clock(), **settings)  # type: ignore[arg-type]


def test_the_defaults_are_eight_cases_ten_minutes_and_a_word_cap() -> None:
    s = reader_search_cache_settings({})
    assert (s.cases, s.ttl_seconds, s.max_words) == (8, 600.0, 3_000_000)


def test_the_settings_come_from_the_environment() -> None:
    s = reader_search_cache_settings(
        {
            "READER_SEARCH_CACHE_CASES": "2",
            "READER_SEARCH_CACHE_TTL_SECONDS": "30",
            "READER_SEARCH_CACHE_MAX_WORDS": "99",
        }
    )
    assert (s.cases, s.ttl_seconds, s.max_words) == (2, 30.0, 99)
    with pytest.raises(ConfigError, match="READER_SEARCH_CACHE_CASES"):
        reader_search_cache_settings({"READER_SEARCH_CACHE_CASES": "0"})


async def test_the_same_case_and_version_loads_once() -> None:
    c, loader, case = cache(), Loader(), uuid.uuid4()
    first = await c.get_words(case, loader, version=("v1",))
    second = await c.get_words(case, loader, version=("v1",))
    assert loader.calls == 1
    assert second is first


async def test_a_new_version_loads_again() -> None:
    c, loader, case = cache(), Loader(), uuid.uuid4()
    await c.get_words(case, loader, version=("v1",))
    await c.get_words(case, loader, version=("v2",))
    assert loader.calls == 2
    await c.get_words(case, loader, version=("v2",))
    assert loader.calls == 2


async def test_words_expire_after_ten_minutes() -> None:
    clock = Clock()
    c, loader, case = cache(clock), Loader(), uuid.uuid4()
    await c.get_words(case, loader, version=("v",))
    clock.now += 599
    await c.get_words(case, loader, version=("v",))
    assert loader.calls == 1
    clock.now += 2
    await c.get_words(case, loader, version=("v",))
    assert loader.calls == 2


async def test_at_most_the_set_number_of_cases_are_kept_least_recently_used_out() -> None:
    c, loader = cache(max_cases=2), Loader()
    a, b, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    await c.get_words(a, loader, version=("v",))
    await c.get_words(b, loader, version=("v",))
    await c.get_words(a, loader, version=("v",))  # a is now the most recently used
    await c.get_words(d, loader, version=("v",))  # b goes
    assert set(c.case_ids()) == {a, d}
    assert len(c) == 2


async def test_the_total_words_held_are_capped_oldest_out() -> None:
    c, loader = cache(max_words=10), Loader(count=4)
    a, b, d = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    for case in (a, b, d):
        await c.get_words(case, loader, version=("v",))
    assert set(c.case_ids()) == {b, d}  # 12 words would be over 10
    assert c.word_count <= 10


async def test_one_case_over_the_cap_is_answered_but_not_kept() -> None:
    c, loader, case = cache(max_words=5), Loader(count=9), uuid.uuid4()
    got = await c.get_words(case, loader, version=("v",))
    assert got.word_count == 9
    assert len(c) == 0 and c.word_count == 0


async def test_clear_case_drops_only_that_case() -> None:
    c, loader = cache(), Loader()
    a, b = uuid.uuid4(), uuid.uuid4()
    await c.get_words(a, loader, version=("v",))
    await c.get_words(b, loader, version=("v",))
    c.clear_case(a)
    assert set(c.case_ids()) == {b}
    assert c.word_count == 3
    c.clear_case(uuid.uuid4())  # an unknown case is fine
    await c.get_words(a, loader, version=("v",))
    assert loader.calls == 3


async def test_clear_all_empties_the_cache() -> None:
    c, loader = cache(), Loader()
    await c.get_words(uuid.uuid4(), loader, version=("v",))
    c.clear_all()
    assert len(c) == 0 and c.word_count == 0


async def test_a_cleared_case_is_not_refilled_by_a_load_already_running() -> None:
    c, case = cache(), uuid.uuid4()
    started, release = asyncio.Event(), asyncio.Event()

    async def slow() -> CaseWords:
        started.set()
        await release.wait()
        return words()

    task = asyncio.create_task(c.get_words(case, slow, version=("v",)))
    await started.wait()
    c.clear_case(case)  # the case key was destroyed while the words were being opened
    release.set()
    await task
    assert len(c) == 0


async def test_searches_arriving_together_share_one_load() -> None:
    c, loader, case = cache(), Loader(), uuid.uuid4()
    results = await asyncio.gather(*(c.get_words(case, loader, version=("v",)) for _ in range(5)))
    assert loader.calls == 1
    assert all(r is results[0] for r in results)


async def test_a_failed_load_is_not_kept_and_the_next_search_tries_again() -> None:
    c, case = cache(), uuid.uuid4()

    async def broken() -> CaseWords:
        raise RuntimeError("no key")

    with pytest.raises(RuntimeError):
        await c.get_words(case, broken, version=("v",))
    loader = Loader()
    await c.get_words(case, loader, version=("v",))
    assert loader.calls == 1


async def test_the_cache_is_keyed_by_case_id_only() -> None:
    c = cache()
    await c.get_words(uuid.uuid4(), Loader(), version=("v",))
    assert all(isinstance(k, uuid.UUID) for k in c.case_ids())


async def test_the_shared_cache_can_be_cleared_for_a_case(monkeypatch: pytest.MonkeyPatch) -> None:
    """What PR 15 calls when a case key is destroyed."""
    shared = cache()
    monkeypatch.setattr(search_cache, "_cache", shared)
    case, loader = uuid.uuid4(), Loader()
    await search_cache.get_words(case, loader, version=("v",))
    search_cache.clear_case(case)
    await search_cache.get_words(case, loader, version=("v",))
    assert loader.calls == 2
    search_cache.clear_all()
    assert len(shared) == 0
