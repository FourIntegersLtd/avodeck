"""Plaintext on disk (audit 13, DP-06): named scratch files for tools that need a path, and the start sweep."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from app.core import case_files
from app.core.case_files import scratch_file, sweep_scratch


@pytest.fixture
def scratch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "scratch"
    monkeypatch.setattr(case_files, "AVODECK_SCRATCH_DIR", str(directory))
    return directory


def test_a_scratch_file_is_0600_in_a_0700_directory_and_gone_afterwards(scratch: Path) -> None:
    with scratch_file(b"%PDF synthetic", suffix=".pdf") as path:
        assert path.endswith(".pdf") and Path(path).parent == scratch
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        assert stat.S_IMODE(os.stat(scratch).st_mode) == 0o700
        assert Path(path).read_bytes() == b"%PDF synthetic"
    assert list(scratch.iterdir()) == []


def test_the_file_is_removed_when_the_body_fails(scratch: Path) -> None:
    with pytest.raises(RuntimeError):
        with scratch_file(b"x"):
            raise RuntimeError("boom")
    assert list(scratch.iterdir()) == []


def test_two_files_never_share_a_name(scratch: Path) -> None:
    with scratch_file(b"a") as one, scratch_file(b"b") as two:
        assert one != two


def test_sweep_removes_leftovers_of_a_dead_run_and_nothing_else(scratch: Path) -> None:
    scratch.mkdir(mode=0o700)
    leftover = scratch / f"plain-{os.getpid()}-abc.pdf"  # an earlier run of this process id (a restarted container)
    dead = scratch / "plain-2147483646-abc.pdf"
    other = scratch / "notes.txt"
    for path in (leftover, dead, other):
        path.write_bytes(b"x")
    removed = sweep_scratch()
    assert removed == 2
    assert [p.name for p in scratch.iterdir()] == ["notes.txt"]


def test_sweep_leaves_a_live_other_process_alone(scratch: Path) -> None:
    scratch.mkdir(mode=0o700)
    parent = scratch / f"plain-{os.getppid()}-abc.pdf"  # the parent of this process is alive
    parent.write_bytes(b"x")
    assert sweep_scratch() == 0
    assert parent.exists()


def test_sweep_with_no_directory_yet_removes_nothing(scratch: Path) -> None:
    assert sweep_scratch() == 0
