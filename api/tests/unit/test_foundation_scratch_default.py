"""With no `AVODECK_SCRATCH_DIR`, plaintext scratch files go in a private directory of ours, not the shared temp
directory itself (audit 13 reviewer round, F-i). The directory is `<temp>/avodeck-scratch-<uid>`, `0700`, owned by
this account; one that is a link or owned by someone else is refused."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

import pytest

from app.core import case_files
from app.core.case_files import scratch_file, sweep_scratch


@pytest.fixture
def shared_temp(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(case_files, "AVODECK_SCRATCH_DIR", "")
    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    return tmp_path


def test_the_default_scratch_directory_is_a_private_one_of_ours(shared_temp: Path) -> None:
    with scratch_file(b"zz") as path:
        parent = Path(path).parent
        assert parent == shared_temp / f"avodeck-scratch-{os.getuid()}"
        assert stat.S_IMODE(parent.stat().st_mode) == 0o700
        assert Path(path).read_bytes() == b"zz"
    assert not Path(path).exists()


def test_an_existing_default_directory_with_open_permissions_is_tightened(shared_temp: Path) -> None:
    ours = shared_temp / f"avodeck-scratch-{os.getuid()}"
    ours.mkdir(mode=0o777)
    ours.chmod(0o777)
    with scratch_file(b"zz"):
        pass
    assert stat.S_IMODE(ours.stat().st_mode) == 0o700


def test_a_link_in_the_place_of_the_default_directory_is_refused(shared_temp: Path) -> None:
    elsewhere = shared_temp / "elsewhere"
    elsewhere.mkdir()
    (shared_temp / f"avodeck-scratch-{os.getuid()}").symlink_to(elsewhere)
    with pytest.raises(OSError), scratch_file(b"zz"):
        pass
    assert list(elsewhere.iterdir()) == []


def test_the_sweep_looks_in_the_same_directory(shared_temp: Path) -> None:
    ours = shared_temp / f"avodeck-scratch-{os.getuid()}"
    ours.mkdir(mode=0o700)
    (ours / "plain-999999999-zz").write_bytes(b"left behind")  # a process id that is not running
    assert sweep_scratch() == 1
    assert list(ours.iterdir()) == []
