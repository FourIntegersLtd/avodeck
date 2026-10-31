"""Which local Supabase the tests use: this checkout's own project, so parallel worktrees never share one.

`supabase status -o env` run in the checkout names its database (DB_URL); only when that fails do
the tests fall back to the default local port, but only in the main checkout: a linked git worktree
never shares the main checkout's database, so there it fails instead. TEST_DATABASE_* variables, when set,
always win.
"""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

DEFAULT_DB_PORT = "55322"
# The main checkout's app database and the founder's manual-testing database: a worktree whose
# config.toml keeps the main project id would otherwise be handed one of them by `supabase status`.
SHARED_DB_PORTS = frozenset({DEFAULT_DB_PORT, "56322"})
_DB_URL = re.compile(r'^DB_URL="?postgresql://[^@]+@[^:/]+:(\d+)/', re.MULTILINE)

# The local-only role passwords set in supabase/seed.sql; never real credentials.
_ROLES = {
    "TEST_DATABASE_ADMIN_URL": "postgres:postgres",
    "TEST_DATABASE_URL": "app_api:app_api_local",
    "TEST_DATABASE_URL_DIRECT": "app_worker:app_worker_local",
}


def db_port_from_status(status: str) -> str | None:
    match = _DB_URL.search(status)
    return match.group(1) if match else None


def is_linked_worktree(repo: Path) -> bool:
    """A linked worktree's `.git` is a file pointing at the main repository; a main checkout's is a directory."""
    return (repo / ".git").is_file()


def local_db_port(repo: Path, run: Callable[..., Any] = subprocess.run) -> str:
    port: str | None = None
    try:
        done = run(["supabase", "status", "-o", "env"], cwd=repo, capture_output=True, text=True, check=False)
        if done.returncode == 0:
            port = db_port_from_status(done.stdout or "")
    except OSError:
        pass
    linked = is_linked_worktree(repo)
    if port and not (linked and port in SHARED_DB_PORTS):
        return port
    if linked:
        raise RuntimeError(
            f"No local Supabase found for the worktree {repo}, and the default port ({DEFAULT_DB_PORT}) is the "
            "main checkout's database. Set TEST_DATABASE_ADMIN_URL, TEST_DATABASE_URL and "
            "TEST_DATABASE_URL_DIRECT, or start this worktree's own Supabase (`supabase start`)."
        )
    return DEFAULT_DB_PORT


def test_dsns(port: str, env: Mapping[str, str]) -> dict[str, str]:
    """The three test DSNs: from `env` where it names them, else the local roles on `port`."""
    return {name: env.get(name) or f"postgresql://{cred}@127.0.0.1:{port}/postgres" for name, cred in _ROLES.items()}


test_dsns.__test__ = False  # type: ignore[attr-defined]  # a helper, not a test, wherever it is imported
