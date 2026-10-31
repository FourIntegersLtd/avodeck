"""`spool()` gives a seekable consumer a decrypted copy of a stored document without leaving a path behind.

The scratch file is unlinked as soon as it is open (so a crash or restart leaves nothing to find),
removed on every exit, capped at the upload limit, and bounded by a process-wide number of slots.
Content here is made-up bytes; nothing is logged.
"""

from __future__ import annotations

import os
import stat
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import anyio
import pytest

from app.core import case_files
from app.core.case_files import SpoolTooLarge, StoredDocument, spool_chunks

pytestmark = pytest.mark.anyio


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "scratch"  # does not exist yet: spool creates it private
    monkeypatch.setattr(case_files, "AVODECK_SCRATCH_DIR", str(directory))
    monkeypatch.setattr(case_files, "_slots", anyio.Semaphore(2))
    return directory


async def pieces(*blobs: bytes) -> AsyncIterator[bytes]:
    for blob in blobs:
        yield blob


async def test_spooled_content_is_seekable_and_no_path_is_visible(scratch: Path) -> None:
    async with spool_chunks(pieces(b"abc", b"def"), max_memory=0) as f:
        assert list(scratch.iterdir()) == []  # unlinked while still open
        assert f.read() == b"abcdef"
        f.seek(2)
        assert f.read(2) == b"cd"
    assert list(scratch.iterdir()) == []


async def test_scratch_directory_is_private_and_small_content_stays_in_memory(scratch: Path) -> None:
    async with spool_chunks(pieces(b"tiny"), max_memory=1024) as f:
        assert f.read() == b"tiny"
    assert stat.S_IMODE(os.stat(scratch).st_mode) == 0o700


async def test_scratch_file_is_private_to_the_owner(scratch: Path) -> None:
    async with spool_chunks(pieces(b"abc"), max_memory=0) as f:
        assert stat.S_IMODE(os.fstat(f.fileno()).st_mode) == 0o600


async def test_cleans_up_when_the_consumer_raises(scratch: Path) -> None:
    with pytest.raises(RuntimeError):
        async with spool_chunks(pieces(b"abc"), max_memory=0) as f:
            raise RuntimeError("consumer failed")
    assert f.closed
    assert list(scratch.iterdir()) == []


async def test_cleans_up_when_the_source_fails_midway(scratch: Path) -> None:
    async def broken() -> AsyncIterator[bytes]:
        yield b"abc"
        raise ValueError("chunk missing")

    with pytest.raises(ValueError):
        async with spool_chunks(broken(), max_memory=0):
            pytest.fail("must not be entered")
    assert list(scratch.iterdir()) == []


async def test_refuses_content_past_the_cap(scratch: Path) -> None:
    with pytest.raises(SpoolTooLarge):
        async with spool_chunks(pieces(b"abcd", b"efgh"), max_memory=0, max_size=6):
            pytest.fail("must not be entered")
    assert list(scratch.iterdir()) == []


async def test_content_exactly_at_the_cap_is_accepted(scratch: Path) -> None:
    async with spool_chunks(pieces(b"abcd", b"ef"), max_memory=0, max_size=6) as f:
        assert f.read() == b"abcdef"


async def test_a_slot_is_held_until_the_spool_closes(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_files, "_slots", anyio.Semaphore(1))
    order: list[str] = []
    release = anyio.Event()

    async def first() -> None:
        async with spool_chunks(pieces(b"a"), max_memory=0):
            order.append("first in")
            await release.wait()
        order.append("first out")

    async def second() -> None:
        async with spool_chunks(pieces(b"b"), max_memory=0):
            order.append("second in")

    async with anyio.create_task_group() as tg:
        tg.start_soon(first)
        await anyio.sleep(0.05)
        tg.start_soon(second)
        await anyio.sleep(0.05)
        assert order == ["first in"]  # second is waiting for a slot
        release.set()
    assert order == ["first in", "first out", "second in"]


async def test_the_slot_is_given_back_after_a_failure(scratch: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(case_files, "_slots", anyio.Semaphore(1))
    with pytest.raises(SpoolTooLarge):
        async with spool_chunks(pieces(b"abcdef"), max_memory=0, max_size=2):
            pass
    with anyio.fail_after(1):
        async with spool_chunks(pieces(b"ok"), max_memory=0) as f:
            assert f.read() == b"ok"


async def test_a_low_disk_warning_is_logged_without_paths(
    scratch: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(case_files, "_free_bytes", lambda _directory: 10 * 1024 * 1024)
    with caplog.at_level("WARNING", logger="app.core.case_files"):
        case_files.warn_if_scratch_low()
    assert [r.levelname for r in caplog.records] == ["WARNING"]
    assert str(scratch) not in caplog.text


async def test_stored_document_spool_decrypts_every_chunk_in_order(scratch: Path) -> None:
    class FakeStorage:
        async def get(self, path: str) -> bytes:
            return path.encode()

    class FakeCrypto:
        async def decrypt(self, _conn: Any, _case: uuid.UUID, blob: bytes, *, purpose: str) -> bytes:
            assert blob == purpose.encode()
            return blob[-2:]

    doc = StoredDocument(
        storage=FakeStorage(),  # type: ignore[arg-type]
        crypto=FakeCrypto(),  # type: ignore[arg-type]
        case_id=uuid.uuid4(),
        prefix="p",
        paths=["p/01", "p/02"],
    )
    async with doc.spool(None, max_memory=0) as f:  # type: ignore[arg-type]
        assert f.read() == b"0102"
