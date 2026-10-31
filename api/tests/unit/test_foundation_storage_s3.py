"""How the S3 adapter behaves where S3 differs from the Supabase Storage it replaces (ADR 0032).

Real HTTP against a local S3 stand-in (moto). Keys are unique per test, so tests share one bucket.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import datetime

import pytest

from app.core.storage import CaseFileStorage, StorageNotFound, case_prefix, storage_settings
from tests.support.files import local_storage, storage_env

pytestmark = pytest.mark.anyio


@pytest.fixture
async def store() -> AsyncIterator[CaseFileStorage]:
    storage = await local_storage(list_page_size=2, delete_batch_size=1000)
    try:
        yield storage
    finally:
        await storage.aclose()


def prefix() -> str:
    return case_prefix(uuid.uuid4())


async def test_put_overwrites_an_existing_key(store: CaseFileStorage) -> None:
    key = f"{prefix()}/a.bin"
    await store.put(key, b"first")
    await store.put(key, b"second")
    assert await store.get(key) == b"second"


async def test_get_of_a_missing_key_is_storage_not_found(store: CaseFileStorage) -> None:
    with pytest.raises(StorageNotFound):
        await store.get(f"{prefix()}/missing.bin")


async def test_get_from_a_missing_bucket_is_storage_not_found() -> None:
    storage = CaseFileStorage(storage_settings(storage_env(STORAGE_BUCKET=f"no-such-{uuid.uuid4().hex[:12]}")))
    try:
        with pytest.raises(StorageNotFound):
            await storage.get("a/b")
    finally:
        await storage.aclose()


async def test_list_returns_only_objects_directly_under_the_prefix_in_name_order(store: CaseFileStorage) -> None:
    base = prefix()
    for name in ("c.bin", "a.bin", "b.bin", "d.bin", "sub/deep.bin"):
        await store.put(f"{base}/{name}", b"x")
    listed = await store.list(base)
    assert listed == [f"{base}/{n}" for n in ("a.bin", "b.bin", "c.bin", "d.bin")]
    assert all(not key.endswith("/") for key in listed)


async def test_list_folders_returns_names_not_paths(store: CaseFileStorage) -> None:
    base = prefix()
    for name in ("uploads/u1/1.part", "uploads/u2/1.part", "pages/1.png", "top.bin"):
        await store.put(f"{base}/{name}", b"x")
    assert sorted(await store.list_folders(base)) == ["pages", "uploads"]
    assert sorted(await store.list_folders(f"{base}/uploads")) == ["u1", "u2"]


async def test_list_object_times_are_aware_and_walk_past_one_page(store: CaseFileStorage) -> None:
    base = prefix()
    keys = [f"{base}/uploads/u/{n:03d}" for n in range(1, 6)] + [f"{base}/x.bin"]
    for key in keys:
        await store.put(key, b"x")
    times = await store.list_object_times(base)
    assert sorted(times) == sorted(keys)
    for moment in times.values():
        assert isinstance(moment, datetime)
        assert moment.tzinfo is not None


async def test_list_recursive_returns_every_key_sorted_past_one_page(store: CaseFileStorage) -> None:
    base = prefix()
    keys = sorted([f"{base}/{n}.bin" for n in range(5)] + [f"{base}/a/b/c/{n}.bin" for n in range(4)])
    for key in reversed(keys):
        await store.put(key, b"x")
    assert await store.list_recursive(base) == keys
    assert await store.list_recursive(prefix()) == []


async def test_delete_of_1001_keys_is_two_batches_and_missing_keys_are_fine(store: CaseFileStorage) -> None:
    base = prefix()
    keys = [f"{base}/k/{n:04d}" for n in range(1001)]
    for key in keys[:20]:
        await store.put(key, b"x")  # the other 981 never existed

    calls: list[int] = []
    original = store._client.delete_objects

    def counting(**kwargs: object) -> object:
        calls.append(len(kwargs["Delete"]["Objects"]))  # type: ignore[index]
        return original(**kwargs)

    store._client.delete_objects = counting
    await store.delete(keys)
    assert calls == [1000, 1]
    assert await store.list_recursive(base) == []
    await store.delete(keys[:3])  # already gone: no error
