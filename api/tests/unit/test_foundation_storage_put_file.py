"""`CaseFileStorage.put_file` stores a file from disk in bounded parts (scalability PR 13 leftover).

The export's sealed archive can be as large as the whole bundle. `put(path, bytes)` needs all of it in memory;
`put_file(path, source)` reads `part_size` at a time and sends it as one part of an S3 multipart upload. A
small file goes up in one call. Server-side encryption stays on, any failure aborts the upload (nothing is left
half-written and billed), and errors carry the code only, never the key (rule 6).
"""

from __future__ import annotations

import asyncio
import io
import os
import tempfile
import tracemalloc
import uuid
from collections.abc import AsyncIterator
from typing import Any

import pytest
from botocore.exceptions import ClientError

from app.core.storage import CaseFileStorage, StorageError
from tests.support.files import STORAGE_BUCKET, local_storage

pytestmark = pytest.mark.anyio

MIB = 1024 * 1024


class FakeS3:
    """Records every call and keeps no body, so memory measured is the adapter's."""

    def __init__(self, *, fail_on_part: int | None = None, error: BaseException | None = None) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.sizes: list[int] = []
        self.fail_on_part = fail_on_part
        self.error = error

    def _note(self, name: str, kwargs: dict[str, Any]) -> None:
        shown = {k: v for k, v in kwargs.items() if k != "Body"}
        self.calls.append((name, shown))

    def names(self) -> list[str]:
        return [n for n, _ in self.calls]

    def put_object(self, **kwargs: Any) -> dict[str, Any]:
        self._note("put_object", kwargs)
        self.sizes.append(len(kwargs["Body"]))
        return {}

    def create_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self._note("create_multipart_upload", kwargs)
        return {"UploadId": "upload-1"}

    def upload_part(self, **kwargs: Any) -> dict[str, Any]:
        self._note("upload_part", kwargs)
        if self.fail_on_part == kwargs["PartNumber"]:
            raise self.error or ClientError({"Error": {"Code": "InternalError"}}, "UploadPart")
        self.sizes.append(len(kwargs["Body"]))
        return {"ETag": f'"etag-{kwargs["PartNumber"]}"'}

    def complete_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self._note("complete_multipart_upload", kwargs)
        return {}

    def abort_multipart_upload(self, **kwargs: Any) -> dict[str, Any]:
        self._note("abort_multipart_upload", kwargs)
        return {}

    def close(self) -> None:
        pass


class Generated(io.RawIOBase):
    """A file of `size` bytes made up as it is read: it holds nothing, so it adds no memory of its own."""

    def __init__(self, size: int, max_read: int | None = None) -> None:
        self.left = size
        self.max_read = max_read

    def readable(self) -> bool:
        return True

    def read(self, n: int = -1) -> bytes:
        n = self.left if n < 0 else min(n, self.left)
        if self.max_read is not None:
            n = min(n, self.max_read)  # a source that hands back short reads
        self.left -= n
        return b"\x07" * n


@pytest.fixture
async def store() -> AsyncIterator[CaseFileStorage]:
    storage = await local_storage()
    try:
        yield storage
    finally:
        await storage.aclose()


def fake(store: CaseFileStorage, **kwargs: Any) -> FakeS3:
    client = FakeS3(**kwargs)
    store._client = client
    return client


async def test_a_large_file_goes_up_as_bounded_parts_in_order(store: CaseFileStorage) -> None:
    client = fake(store)
    path = f"cases/{uuid.uuid4()}/exports/x/full.zip"
    await store.put_file(path, Generated(10_500), part_size=1_000)

    assert client.names() == ["create_multipart_upload"] + ["upload_part"] * 11 + ["complete_multipart_upload"]
    assert client.sizes == [1_000] * 10 + [500]
    create = client.calls[0][1]
    assert create["Bucket"] == store.settings.bucket and create["Key"] == path
    assert create["ServerSideEncryption"] == "AES256"
    parts = [kw for n, kw in client.calls if n == "upload_part"]
    assert [p["PartNumber"] for p in parts] == list(range(1, 12))
    assert {p["UploadId"] for p in parts} == {"upload-1"}
    complete = client.calls[-1][1]
    assert complete["MultipartUpload"]["Parts"] == [{"ETag": f'"etag-{i}"', "PartNumber": i} for i in range(1, 12)]


async def test_short_reads_from_the_source_still_make_full_parts(store: CaseFileStorage) -> None:
    client = fake(store)
    await store.put_file("cases/x/f", Generated(2_500, max_read=300), part_size=1_000)
    assert client.sizes == [1_000, 1_000, 500]


async def test_a_file_that_ends_on_a_part_boundary_has_no_empty_last_part(store: CaseFileStorage) -> None:
    client = fake(store)
    await store.put_file("cases/x/f", Generated(3_000), part_size=1_000)
    assert client.sizes == [1_000, 1_000, 1_000]


async def test_a_file_smaller_than_one_part_is_one_plain_put_with_encryption(store: CaseFileStorage) -> None:
    client = fake(store)
    await store.put_file("cases/x/f", Generated(700), part_size=1_000)
    assert client.names() == ["put_object"] and client.sizes == [700]
    assert client.calls[0][1]["ServerSideEncryption"] == "AES256"


async def test_an_empty_file_is_stored_as_an_empty_object(store: CaseFileStorage) -> None:
    client = fake(store)
    await store.put_file("cases/x/f", Generated(0), part_size=1_000)
    assert client.names() == ["put_object"] and client.sizes == [0]


async def test_a_failed_part_aborts_the_upload_and_names_no_key(store: CaseFileStorage) -> None:
    client = fake(store, fail_on_part=3)
    path = f"cases/{uuid.uuid4()}/exports/secret-looking/full.zip"
    with pytest.raises(StorageError) as caught:
        await store.put_file(path, Generated(10_000), part_size=1_000)
    assert "InternalError" in str(caught.value) and "secret-looking" not in str(caught.value)
    assert client.names().count("abort_multipart_upload") == 1
    assert "complete_multipart_upload" not in client.names()
    assert client.calls[-1][1]["UploadId"] == "upload-1"


async def test_a_source_that_fails_midway_aborts_the_upload(store: CaseFileStorage) -> None:
    client = fake(store)

    class Breaks(Generated):
        def read(self, n: int = -1) -> bytes:
            if self.left < 8_000:
                raise OSError("disk went away")
            return super().read(n)

    with pytest.raises(OSError):
        await store.put_file("cases/x/f", Breaks(10_000), part_size=1_000)
    assert client.names().count("abort_multipart_upload") == 1
    assert "complete_multipart_upload" not in client.names()


async def test_a_cancelled_upload_is_aborted_too(store: CaseFileStorage) -> None:
    started = asyncio.Event()
    client = fake(store)
    real = client.upload_part

    def slow(**kwargs: Any) -> dict[str, Any]:
        started.set()
        import time

        time.sleep(0.3)
        return real(**kwargs)

    client.upload_part = slow  # type: ignore[method-assign]
    task = asyncio.create_task(store.put_file("cases/x/f", Generated(5_000), part_size=1_000))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert client.names().count("abort_multipart_upload") == 1


async def test_peak_memory_stays_near_one_part_for_a_file_many_times_larger(store: CaseFileStorage) -> None:
    fake(store)
    part, size = 8 * MIB, 96 * MIB
    tracemalloc.start()
    try:
        await store.put_file("cases/x/f", Generated(size), part_size=part)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 3 * part, f"peak {peak / MIB:.1f} MiB for a {size // MIB} MiB file in {part // MIB} MiB parts"


async def test_the_default_part_size_is_a_valid_s3_part(store: CaseFileStorage) -> None:
    from app.core import storage as storage_module

    assert 5 * MIB <= storage_module.PART_SIZE <= 64 * MIB


async def test_a_real_round_trip_stores_the_same_bytes_encrypted_at_rest_with_no_upload_left(
    store: CaseFileStorage,
) -> None:
    data = os.urandom(12 * MIB + 321)
    path = f"cases/{uuid.uuid4()}/exports/x/full.zip"
    with tempfile.TemporaryFile() as source:
        source.write(data)
        source.seek(0)
        await store.put_file(path, source, part_size=5 * MIB)
    assert await store.get(path) == data
    head = store._client.head_object(Bucket=STORAGE_BUCKET, Key=path)
    assert head["ServerSideEncryption"] == "AES256"
    assert store._client.list_multipart_uploads(Bucket=STORAGE_BUCKET).get("Uploads", []) == []


async def test_a_real_failure_leaves_no_upload_and_no_object(store: CaseFileStorage) -> None:
    path = f"cases/{uuid.uuid4()}/exports/x/full.zip"

    class Breaks(Generated):
        def read(self, n: int = -1) -> bytes:
            if self.left < 4 * MIB:
                raise OSError("disk went away")
            return super().read(n)

    with pytest.raises(OSError):
        await store.put_file(path, Breaks(11 * MIB), part_size=5 * MIB)
    assert store._client.list_multipart_uploads(Bucket=STORAGE_BUCKET).get("Uploads", []) == []
    assert await store.list_recursive(path.rsplit("/", 1)[0]) == []
