"""All four listing methods read every page (three keys a page here) and give the same answers."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.storage import CaseFileStorage
from tests.support.files import local_storage

pytestmark = pytest.mark.anyio


@pytest.fixture
async def store() -> AsyncIterator[CaseFileStorage]:
    storage = await local_storage(list_page_size=3)
    try:
        yield storage
    finally:
        await storage.aclose()


async def test_every_listing_method_reads_all_pages(store: CaseFileStorage) -> None:
    base = f"cases/{uuid.uuid4()}"
    direct = [f"{base}/a/{n:03d}" for n in range(1, 8)]
    deep = [f"{base}/a/sub{n}/x" for n in range(1, 5)]
    for key in [*direct, *deep]:
        await store.put(key, b"x")

    assert await store.list(f"{base}/a") == direct
    assert await store.list_folders(f"{base}/a") == [f"sub{n}" for n in range(1, 5)]
    assert await store.list_recursive(f"{base}/a") == sorted([*direct, *deep])
    times = await store.list_object_times(f"{base}/a")
    assert sorted(times) == sorted([*direct, *deep])
    assert all(t is not None for t in times.values())


async def test_an_empty_prefix_lists_nothing(store: CaseFileStorage) -> None:
    base = f"cases/{uuid.uuid4()}/none"
    assert await store.list(base) == []
    assert await store.list_folders(base) == []
    assert await store.list_recursive(base) == []
    assert await store.list_object_times(base) == {}
