"""Storage listing pages through every result, and a missing object is its own error (stream A).

Runs against a local S3 stand-in (moto) over real HTTP, with a small page size so paging stays cheap.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.storage import CaseFileStorage, StorageError, StorageNotFound, storage_settings
from tests.support.files import local_storage, storage_env

pytestmark = pytest.mark.anyio


@pytest.fixture
async def store() -> AsyncIterator[CaseFileStorage]:
    storage = await local_storage(list_page_size=3)
    try:
        yield storage
    finally:
        await storage.aclose()


async def test_list_reads_every_page_not_just_the_first(store: CaseFileStorage) -> None:
    base = f"cases/{uuid.uuid4()}/uploads/y"
    names = [f"{n:05d}" for n in range(1, 8)]
    for n in names:
        await store.put(f"{base}/{n}", b"x")
    assert await store.list(f"{base}/") == [f"{base}/{n}" for n in names]


async def test_a_missing_object_is_storage_not_found(store: CaseFileStorage) -> None:
    with pytest.raises(StorageNotFound):
        await store.get(f"cases/{uuid.uuid4()}/nope")


async def test_other_failures_are_plain_storage_errors() -> None:
    # nothing listens on this port: a connection failure is a StorageError, not "not found"
    env = storage_env(S3_ENDPOINT_URL="http://127.0.0.1:9")
    storage = CaseFileStorage(storage_settings(env))
    try:
        with pytest.raises(StorageError) as err:
            await storage.get("a/b")
        assert not isinstance(err.value, StorageNotFound)
    finally:
        await storage.aclose()
