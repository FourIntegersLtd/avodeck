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


def test_a_linked_worktree_on_the_main_project_is_refused(tmp_path: Path) -> None:
    """A worktree still on the committed project_id would reset the main checkout's database."""
    mod = _load()
    worktree = _checkout(tmp_path / "wt", "avodeck")
    (worktree / ".git").write_text("gitdir: /elsewhere/.git/worktrees/wt\n")
    with pytest.raises(SystemExit) as exc:
        mod.assert_own_project(str(worktree))
    assert exc.value.code != 0

    own = _checkout(tmp_path / "own", "avodeck-a")
    (own / ".git").write_text("gitdir: /elsewhere/.git/worktrees/own\n")
    mod.assert_own_project(str(own))

    main = _checkout(tmp_path / "main", "avodeck")
    (main / ".git").mkdir()
    mod.assert_own_project(str(main))


def _commit(repo: Path, msg: str, n: int) -> str:
    import os
    import subprocess

    (repo / f"f{n}.txt").write_text(msg)
    when = f"2026-01-01T00:00:{n:02d}"
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when}

    def run(*a: str) -> str:
        return subprocess.run(["git", *a], cwd=repo, env=env, check=True, capture_output=True, text=True).stdout.strip()

    run("add", "-A")
    run("-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", msg)
    return run("rev-parse", "HEAD")


def test_a_feat_is_paired_with_its_own_parent_across_merged_branches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two branches merged in: the commit listed just before a feat may be the other branch's test."""
    import subprocess

    mod = _load()
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*a: str) -> None:
        subprocess.run(["git", *a], cwd=repo, check=True, capture_output=True)

    git("init", "-q", "-b", "main")
    base = _commit(repo, "chore: start", 1)
    git("checkout", "-q", "-b", "one")
    t1 = _commit(repo, "test(a): one", 2)
    git("checkout", "-q", "-b", "two", base)
    t2 = _commit(repo, "test(b): two", 3)
    git("checkout", "-q", "one")
    f1 = _commit(repo, "feat(a): one", 4)
    git("checkout", "-q", "two")
    f2 = _commit(repo, "feat(b): two", 5)
    git("checkout", "-q", "main")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "merge", "-q", "--no-ff", "-m", "Merge one", "one")
    git("-c", "user.name=t", "-c", "user.email=t@example.com", "merge", "-q", "--no-ff", "-m", "Merge two", "two")
    monkeypatch.chdir(repo)
    assert dict(mod.feat_pairs(base)) == {f1: t1, f2: t2}


def test_the_known_historic_commit_is_excused() -> None:
    """d6784a1's tests failed only from a test-order bug, fixed in cac27bd; pushed history is not rewritten."""
    assert "d6784a1789899552f84afb7a2078e9b2bfdeba7e" in _load().EXCUSED
