"""Finding and removing every file of a case in private Storage (slice H deletes a case with these).

- `case_prefix(case_id)` is the one folder holding everything of a case; uploads live under it.
- `list_recursive(prefix)` lists every object below a folder, however deep and however many
  (Storage lists one folder level at a time, a page at a time).
- `delete(paths)` removes any number of objects, in batches.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest

from app.core.storage import SupabaseStorage, case_prefix, storage_settings, upload_prefix
from tests.support.files import local_storage, storage_env

pytestmark = pytest.mark.anyio


@pytest.fixture
async def small_pages(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    await (await local_storage(auth_env)).aclose()  # makes sure the test bucket exists
    store = SupabaseStorage(storage_settings(storage_env(auth_env)), list_page_size=2, delete_batch_size=2)
    try:
        yield store
    finally:
        await store.aclose()


def test_a_cases_files_all_live_under_its_prefix() -> None:
    case, upload = uuid.uuid4(), uuid.uuid4()
    assert case_prefix(case) == f"cases/{case}"
    assert upload_prefix(case, upload).startswith(case_prefix(case) + "/")


async def test_list_recursive_finds_every_object_below_a_prefix(small_pages: SupabaseStorage) -> None:
    case, other = uuid.uuid4(), uuid.uuid4()
    prefix = case_prefix(case)
    paths = sorted(
        [f"{prefix}/a.bin", f"{prefix}/b.bin", f"{prefix}/c.bin"]
        + [f"{prefix}/uploads/u1/{n}.part" for n in range(1, 6)]
        + [f"{prefix}/pages/1/deep/x.png"]
    )
    for path in paths:
        await small_pages.put(path, b"x")
    await small_pages.put(f"{case_prefix(other)}/a.bin", b"x")

    assert sorted(await small_pages.list_recursive(prefix)) == paths
    assert await small_pages.list_recursive(case_prefix(uuid.uuid4())) == []

    await small_pages.delete(paths)
    assert await small_pages.list_recursive(prefix) == []
    assert await small_pages.list_recursive(case_prefix(other)) == [f"{case_prefix(other)}/a.bin"]
    await small_pages.delete([f"{case_prefix(other)}/a.bin"])
