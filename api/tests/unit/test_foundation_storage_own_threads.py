"""File storage calls run on their own threads, so slow work elsewhere cannot hold them up."""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator
from concurrent.futures import ThreadPoolExecutor

import pytest

from app.core.storage import CaseFileStorage
from tests.support.files import local_storage

pytestmark = pytest.mark.anyio


@pytest.fixture
async def store() -> AsyncIterator[CaseFileStorage]:
    storage = await local_storage()
    try:
        yield storage
    finally:
        await storage.aclose()


async def test_storage_works_while_every_default_thread_is_busy(store: CaseFileStorage) -> None:
    loop = asyncio.get_running_loop()
    default = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(default)
    release = threading.Event()
    blockers = [loop.run_in_executor(None, release.wait, 10) for _ in range(2)]
    try:
        await asyncio.sleep(0.2)  # both default threads are now waiting
        path = f"cases/{uuid.uuid4()}/probe"
        await asyncio.wait_for(store.put(path, b"x"), timeout=3)
        assert await asyncio.wait_for(store.get(path), timeout=3) == b"x"
    finally:
        release.set()
        await asyncio.gather(*blockers)
        default.shutdown(wait=True)
