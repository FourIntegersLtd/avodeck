"""Tests find their own checkout's local Supabase, so parallel worktrees never share a database.

- With no TEST_DATABASE_* variables, the test DSNs use the port of the checkout's own project
  (`supabase status -o env`, DB_URL); only if that fails do they fall back to the default port.
- scripts/check_red_green.py resets and tests each commit against the current checkout's project:
  it copies the checkout's supabase/config.toml (which may carry local port changes) into the
  temporary worktree and passes the checkout's database port to the tests it runs.
"""

from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.local_supabase import DEFAULT_DB_PORT, db_port_from_status, local_db_port, test_dsns

REPO = Path(__file__).resolve().parents[3]
STATUS = 'API_URL="http://127.0.0.1:58421"\nDB_URL="postgresql://postgres:postgres@127.0.0.1:58422/postgres"\n'


def runner(stdout: str, code: int = 0):  # type: ignore[no-untyped-def]
    def run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], code, stdout=stdout, stderr="")

    return run


def failing(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
    raise FileNotFoundError("supabase")


def test_the_port_comes_from_the_status_db_url() -> None:
    assert db_port_from_status(STATUS) == "58422"
    assert db_port_from_status("API_URL=x\n") is None


def test_the_checkouts_own_port_is_used() -> None:
    assert local_db_port(REPO, run=runner(STATUS)) == "58422"


def checkout(tmp_path: Path, linked: bool) -> Path:
    """A main checkout has a `.git` directory; a linked worktree has a `.git` file."""
    if linked:
        (tmp_path / ".git").write_text("gitdir: /elsewhere/.git/worktrees/x\n")
    else:
        (tmp_path / ".git").mkdir()
    return tmp_path


def test_the_default_port_only_when_status_fails_in_the_main_checkout(tmp_path: Path) -> None:
    main = checkout(tmp_path, linked=False)
    assert DEFAULT_DB_PORT == "55322"
    assert local_db_port(main, run=runner("", code=1)) == DEFAULT_DB_PORT
    assert local_db_port(main, run=failing) == DEFAULT_DB_PORT
    assert local_db_port(main, run=runner("API_URL=x\n")) == DEFAULT_DB_PORT


def test_a_linked_worktree_never_falls_back_to_the_main_checkouts_database(tmp_path: Path) -> None:
    worktree = checkout(tmp_path, linked=True)
    assert local_db_port(worktree, run=runner(STATUS)) == "58422"
    for run in (runner("", code=1), failing, runner("API_URL=x\n")):
        with pytest.raises(RuntimeError, match="TEST_DATABASE_"):
            local_db_port(worktree, run=run)


def test_a_linked_worktree_whose_status_names_the_main_or_founder_database_is_refused(tmp_path: Path) -> None:
    """A worktree whose config.toml keeps the main project id resolves to the main checkout's stack."""
    worktree = checkout(tmp_path, linked=True)
    for port in ("55322", "56322"):
        status = f'DB_URL="postgresql://postgres:postgres@127.0.0.1:{port}/postgres"\n'
        with pytest.raises(RuntimeError, match="TEST_DATABASE_"):
            local_db_port(worktree, run=runner(status))


def test_the_dsns_use_the_port_unless_the_environment_names_them() -> None:
    dsns = test_dsns("58422", env={})
    assert dsns == {
        "TEST_DATABASE_ADMIN_URL": "postgresql://postgres:postgres@127.0.0.1:58422/postgres",
        "TEST_DATABASE_URL": "postgresql://app_api:app_api_local@127.0.0.1:58422/postgres",
        "TEST_DATABASE_URL_DIRECT": "postgresql://app_worker:app_worker_local@127.0.0.1:58422/postgres",
    }
    named = test_dsns("58422", env={"TEST_DATABASE_URL": "postgresql://elsewhere"})
    assert named["TEST_DATABASE_URL"] == "postgresql://elsewhere"
    assert named["TEST_DATABASE_ADMIN_URL"].endswith(":58422/postgres")


def red_green() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_red_green", REPO / "scripts" / "check_red_green.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_red_green_copies_the_checkouts_supabase_config(tmp_path: Path) -> None:
    root, work = tmp_path / "root", tmp_path / "work"
    (root / "supabase").mkdir(parents=True)
    (work / "supabase").mkdir(parents=True)
    (root / "supabase" / "config.toml").write_text('project_id = "local-ports"\n')
    (work / "supabase" / "config.toml").write_text('project_id = "committed"\n')
    red_green().use_checkout_config(str(root), str(work))
    assert (work / "supabase" / "config.toml").read_text() == 'project_id = "local-ports"\n'


def test_red_green_passes_the_checkouts_database_to_the_tests() -> None:
    env = red_green().test_env(str(REPO), {"PACKS_DIR": "/elsewhere", "HOME": "/h"}, port="58422")
    assert "PACKS_DIR" not in env and env["HOME"] == "/h"
    assert env["TEST_DATABASE_ADMIN_URL"] == "postgresql://postgres:postgres@127.0.0.1:58422/postgres"
    assert env["TEST_DATABASE_URL"] == "postgresql://app_api:app_api_local@127.0.0.1:58422/postgres"
    assert env["TEST_DATABASE_URL_DIRECT"] == "postgresql://app_worker:app_worker_local@127.0.0.1:58422/postgres"
