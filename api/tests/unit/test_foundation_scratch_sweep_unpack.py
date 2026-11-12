"""The start sweep also removes stale `unpack-*` directories a crash left in the scratch directory (audit 13).

The pipeline unpacks an email or a zip into `unpack-<random>` beside its scratch file and removes it when the
block ends. A crash leaves it behind with decrypted members in it. The name has no process id, so age decides:
only a directory untouched for `STALE_UNPACK_SECONDS` goes, so another live worker's current one is kept.
Nothing is followed through a link and no path is reported.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from app.core import case_files
from app.core.case_files import STALE_UNPACK_SECONDS, sweep_scratch


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "scratch"
    directory.mkdir(mode=0o700)
    monkeypatch.setattr(case_files, "AVODECK_SCRATCH_DIR", str(directory))
    return directory


def old(path: Path) -> None:
    then = time.time() - STALE_UNPACK_SECONDS - 60
    os.utime(path, (then, then))


def unpacked(scratch: Path, name: str, *, stale: bool) -> Path:
    directory = scratch / name
    (directory / "inner").mkdir(parents=True)
    (directory / "inner" / "member.bin").write_bytes(b"zz")
    (directory / "converted.pdf").write_bytes(b"zz")
    if stale:
        old(directory)
    return directory


def test_a_stale_unpack_directory_with_its_contents_is_removed(scratch: Path) -> None:
    gone = unpacked(scratch, "unpack-abc123", stale=True)
    assert sweep_scratch() == 1
    assert not gone.exists()


def test_a_recent_unpack_directory_is_kept(scratch: Path) -> None:
    current = unpacked(scratch, "unpack-abc123", stale=False)
    assert sweep_scratch() == 0
    assert (current / "converted.pdf").exists()


def test_only_unpack_directories_and_plain_files_are_touched(scratch: Path) -> None:
    other = unpacked(scratch, "keep-me", stale=True)
    note = scratch / "notes.txt"
    note.write_bytes(b"x")
    old(note)
    assert sweep_scratch() == 0
    assert other.exists() and note.exists()


def test_a_link_called_unpack_is_not_followed(scratch: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    (outside / "inner").mkdir(parents=True)
    (outside / "inner" / "keep.bin").write_bytes(b"x")
    link = scratch / "unpack-link"
    link.symlink_to(outside)
    sweep_scratch()
    assert (outside / "inner" / "keep.bin").exists()


def test_the_count_adds_files_and_directories(scratch: Path) -> None:
    unpacked(scratch, "unpack-abc123", stale=True)
    (scratch / "plain-2147483646-x.pdf").write_bytes(b"x")  # a dead process's file
    assert sweep_scratch() == 2
