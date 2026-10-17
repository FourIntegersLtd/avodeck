"""check_red_green must reset only the invoking checkout's own Supabase project."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "check_red_green.py"


def _load() -> Any:
    spec = importlib.util.spec_from_file_location("check_red_green", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _checkout(path: Path, project: str) -> Path:
    (path / "supabase").mkdir(parents=True)
    (path / "supabase" / "config.toml").write_text(f'project_id = "{project}"\n[api]\nport = 1\n')
    return path


def test_temp_worktree_gets_the_invoking_checkouts_config(tmp_path: Path) -> None:
    mod = _load()
    root = _checkout(tmp_path / "root", "avodeck-a")
    temp = _checkout(tmp_path / "temp", "avodeck")
    mod.use_root_supabase_config(str(root), str(temp))
    assert mod.project_id(str(temp)) == "avodeck-a"


def test_guard_refuses_a_different_project(tmp_path: Path) -> None:
    mod = _load()
    root = _checkout(tmp_path / "root", "avodeck-a")
    temp = _checkout(tmp_path / "temp", "avodeck")
    with pytest.raises(SystemExit) as exc:
        mod.assert_same_project(str(root), str(temp))
    assert exc.value.code != 0
    mod.use_root_supabase_config(str(root), str(temp))
    mod.assert_same_project(str(root), str(temp))
