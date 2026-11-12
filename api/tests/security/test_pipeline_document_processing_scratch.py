"""Audit 13, DP-06 (pipeline half): the decrypted PDFs the split writes to disk live only in the private scratch
directory (a `0600` file in a `0700` directory, via `core.case_files.scratch_file`) and are gone once the split is
over, however it ends: success, failure or cancellation. Files are made up here.
"""

from __future__ import annotations

import asyncio
import io
import os
import stat
import tempfile
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from docx import Document

from app.core import case_files
from app.features.pipeline import repo as pipeline_repo
from app.features.pipeline import service as pipeline_service
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support.file_lifecycle_documents import text_pdf, upload_bytes

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage


class Seen:
    """What the scratch directory held while each plaintext file was open."""

    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.files: list[tuple[Path, int]] = []  # (path, permission bits while open)


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Seen:
    directory = tmp_path / "scratch"
    monkeypatch.setattr(case_files, "AVODECK_SCRATCH_DIR", str(directory))
    seen = Seen(directory)
    real = case_files.scratch_file

    @contextmanager
    def watching(data: bytes | None = None, *, suffix: str = "") -> Iterator[str]:
        with real(data, suffix=suffix) as name:
            seen.files.append((Path(name), stat.S_IMODE(os.stat(name).st_mode)))
            yield name

    monkeypatch.setattr(case_files, "scratch_file", watching)
    named: list[str] = []
    real_mkstemp = tempfile.mkstemp

    def mkstemp(*args: Any, **kwargs: Any) -> tuple[int, str]:
        named.append(str(kwargs.get("prefix", "")))
        return real_mkstemp(*args, **kwargs)

    monkeypatch.setattr(tempfile, "mkstemp", mkstemp)
    seen.named = named  # type: ignore[attr-defined]
    return seen


def docx_bytes() -> bytes:
    document = Document()
    document.add_paragraph("synthetic note")
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def left_behind(seen: Seen) -> list[str]:
    return sorted(p.name for p in seen.directory.iterdir()) if seen.directory.exists() else []


def assert_private(seen: Seen) -> None:
    assert seen.files, "no plaintext file went through the scratch directory"
    for path, mode in seen.files:
        assert path.parent == seen.directory and mode == 0o600, (path.parent, oct(mode))
    assert not [p for p in seen.named if p.startswith("avodeck-split-")], "a plaintext file was made with mkstemp"  # type: ignore[attr-defined]
    assert seen.directory.stat().st_mode & 0o777 == 0o700


async def test_the_split_writes_its_plaintext_to_the_scratch_directory_and_removes_it(
    h: Harness, scratch: Seen
) -> None:
    await lifecycle.run(h, text_pdf(2, "plain"), "plain.pdf")
    await lifecycle.run(h, docx_bytes(), "note.docx")  # converted, so it takes the second file path as well
    assert len(h.pages()) >= 3
    assert_private(scratch)
    assert left_behind(scratch) == []


async def test_the_scratch_file_is_removed_when_the_split_fails(
    h: Harness, scratch: Seen, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(source: Any) -> int:
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(pipeline_service, "_checked_pages", boom)
    await upload_bytes(h, text_pdf(2, "plain"), filename="plain.pdf")
    await h.drain()
    assert_private(scratch)
    assert left_behind(scratch) == []


async def test_the_scratch_file_is_removed_when_the_download_is_cancelled(
    scratch: Seen, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def parts(*_: Any) -> AsyncIterator[bytes]:
        yield b"%PDF-1.7 first part"
        raise asyncio.CancelledError

    monkeypatch.setattr(pipeline_service, "_chunks_free", parts)
    with pytest.raises(asyncio.CancelledError):
        async with pipeline_service._download(None, None, pipeline_repo.Document(uuid.uuid4(), "prefix")):  # type: ignore[arg-type]
            pytest.fail("the download did not run")
    assert_private(scratch)
    assert left_behind(scratch) == []
