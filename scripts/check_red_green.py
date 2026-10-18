#!/usr/bin/env python3
"""CI check: every feat commit must follow a test commit whose new tests failed before.

For each commit in BASE..HEAD (oldest first):
  - a 'feat(...)' commit must be immediately preceded by a 'test(...)' commit;
  - the test files changed in that test commit must FAIL at the test commit
    and PASS at the feat commit.
API test files run with pytest under the interpreter running this script (CI runs it
via `poetry -P api run`, so the api dependencies are present); web test files with vitest.
Each commit's tests run against the database as that commit's migrations build it: when the
`supabase/` tree differs from the last one loaded, the local Supabase is reset from the commit's
worktree (and reset back to HEAD at the end). Set RED_GREEN_DB_RESET=0 to skip the resets.
Resets and tests use the current checkout's own Supabase project: its supabase/config.toml (which
may carry local, uncommitted port changes) is copied into each temporary worktree, and the tests
get TEST_DATABASE_* for its database port (unless they are already set).
Usage: check_red_green.py <base_ref>
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TEST_PREFIXES = ("api/tests/", "web/")
DEFAULT_DB_PORT = "55322"
MAIN_PROJECT = "avodeck"  # the committed project_id: the main checkout's local database
DB_ROLES = {
    "TEST_DATABASE_ADMIN_URL": "postgres:postgres",
    "TEST_DATABASE_URL": "app_api:app_api_local",
    "TEST_DATABASE_URL_DIRECT": "app_worker:app_worker_local",
}  # the local-only role passwords set in supabase/seed.sql
WEB_TEST_RE = re.compile(r"\.(test|spec)\.tsx?$")
# Feat commits already on main whose check fails for a reason fixed later; pushed history is not rewritten.
EXCUSED = {
    "d6784a1789899552f84afb7a2078e9b2bfdeba7e": "test-order bug in its test commit, fixed in cac27bd",
}


def git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def changed_tests(sha: str) -> list[str]:
    files = git("diff-tree", "--no-commit-id", "--name-only", "--diff-filter=d", "-r", sha).splitlines()
    return [f for f in files if f.startswith(TEST_PREFIXES) and not f.endswith((".json", ".md"))
            and (f.startswith("api/") or WEB_TEST_RE.search(f))]


def root_db_port(root: str) -> str:
    """The database port of the current checkout's local Supabase project."""
    try:
        out = subprocess.run(["supabase", "status", "-o", "env"], cwd=root, capture_output=True, text=True,
                             check=False).stdout
    except OSError:
        return DEFAULT_DB_PORT
    match = re.search(r'^DB_URL="?postgresql://[^@]+@[^:/]+:(\d+)/', out, re.MULTILINE)
    return match.group(1) if match else DEFAULT_DB_PORT


def use_checkout_config(root: str, workdir: str) -> None:
    """Point the temporary worktree at the checkout's own project (its config.toml may differ from HEAD's)."""
    src = os.path.join(root, "supabase", "config.toml")
    if os.path.exists(src):
        os.makedirs(os.path.join(workdir, "supabase"), exist_ok=True)
        shutil.copyfile(src, os.path.join(workdir, "supabase", "config.toml"))


def use_root_supabase_config(root: str, workdir: str) -> None:
    """The temporary worktree checks out the committed config (the main project); use the caller's own."""
    use_checkout_config(root, workdir)


def project_id(workdir: str) -> str:
    text = (Path(workdir) / "supabase" / "config.toml").read_text()
    m = re.search(r'^project_id\s*=\s*"([^"]+)"', text, re.MULTILINE)
    return m.group(1) if m else ""


def assert_same_project(root: str, workdir: str) -> None:
    if project_id(root) != project_id(workdir):
        sys.exit(f"Refusing to reset: project_id {project_id(workdir)!r} in the temporary worktree "
                 f"differs from {project_id(root)!r} in the invoking checkout.")


def assert_own_project(root: str) -> None:
    """A linked worktree on the committed project_id shares the main checkout's database; never reset it."""
    if (Path(root) / ".git").is_file() and project_id(root) == MAIN_PROJECT:
        sys.exit(f"Refusing to reset: this worktree uses the main checkout's project {MAIN_PROJECT!r}. "
                 "Give it its own project_id and ports in supabase/config.toml first.")


def test_env(root: str, base: dict[str, str], port: str | None = None) -> dict[str, str]:
    """The environment for a commit's api tests: the checkout's database, never an inherited PACKS_DIR."""
    # An inherited PACKS_DIR (e.g. from scripts/local_api_env.sh) points at the current
    # checkout's packs, not this commit's; drop it so each commit tests its own packs.
    env = {k: v for k, v in base.items() if k != "PACKS_DIR"}
    if not all(env.get(name) for name in DB_ROLES):
        port = port or root_db_port(root)
        for name, cred in DB_ROLES.items():
            env[name] = env.get(name) or f"postgresql://{cred}@127.0.0.1:{port}/postgres"
    return env


test_env.__test__ = False  # type: ignore[attr-defined]  # not a pytest test when imported


_loaded_db: list[str] = []  # tree hash of the supabase/ folder the local database was last reset from


def reset_db(sha: str, workdir: str, root: str) -> None:
    if os.environ.get("RED_GREEN_DB_RESET") == "0" or not shutil.which("supabase"):
        return
    tree = git("rev-parse", f"{sha}:supabase")
    if _loaded_db and _loaded_db[-1] == tree:
        return
    assert_own_project(root)
    use_checkout_config(root, workdir)
    assert_same_project(root, workdir)
    subprocess.run(["supabase", "db", "reset", "--workdir", workdir], check=True, capture_output=True)
    _loaded_db.append(tree)


def run_tests(sha: str, files: list[str]) -> bool:
    root = git("rev-parse", "--show-toplevel")
    with tempfile.TemporaryDirectory() as tmp:
        git("worktree", "add", "--detach", tmp, sha)
        try:
            ok = True
            reset_db(sha, tmp, root)
            py = [f for f in files if f.startswith("api/") and f.endswith(".py")]
            ts = [f for f in files if f.startswith("web/")]
            if py:
                env = test_env(root, dict(os.environ))
                ok &= subprocess.run([sys.executable, "-m", "pytest", "-q", *[f.removeprefix("api/") for f in py]],
                                     cwd=f"{tmp}/api", env=env).returncode == 0
            if ts:
                unit = [f.removeprefix("web/") for f in ts if not f.startswith("web/e2e/")]
                if unit:
                    subprocess.run(["pnpm", "install", "--frozen-lockfile"], cwd=f"{tmp}/web", check=True)
                    ok &= subprocess.run(["pnpm", "vitest", "run", *unit], cwd=f"{tmp}/web").returncode == 0
            return ok
        finally:
            git("worktree", "remove", "--force", tmp)


def feat_pairs(base: str) -> list[tuple[str, str | None]]:
    """Each feat commit in base..HEAD with its parent (merged branches interleave in rev-list order)."""
    pairs: list[tuple[str, str | None]] = []
    for line in git("rev-list", "--reverse", "--parents", f"{base}..HEAD").splitlines():
        sha, *parents = line.split()
        if git("log", "-1", "--format=%s", sha).startswith("feat"):
            pairs.append((sha, parents[0] if len(parents) == 1 else None))
    return pairs


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    pairs = feat_pairs(base)
    failures = []
    for sha, prev in pairs:
        if sha in EXCUSED:
            print(f"{sha[:8]} skipped: {EXCUSED[sha]}")
            continue
        msg = git("log", "-1", "--format=%s", sha)
        if not prev or not git("log", "-1", "--format=%s", prev).startswith("test"):
            failures.append(f"{sha[:8]} '{msg}' is not preceded by a test commit")
            continue
        tests = changed_tests(prev)
        if not tests:
            failures.append(f"{prev[:8]} adds no test files")
            continue
        if run_tests(prev, tests):
            failures.append(f"{prev[:8]} tests already passed before the feature (not red)")
        if not run_tests(sha, tests):
            failures.append(f"{sha[:8]} tests from {prev[:8]} do not pass (not green)")
    if _loaded_db:
        root = git("rev-parse", "--show-toplevel")
        if _loaded_db[-1] != git("rev-parse", "HEAD:supabase"):
            subprocess.run(["supabase", "db", "reset", "--workdir", root], check=True, capture_output=True)
    if failures:
        print("Red-then-green check failed:\n- " + "\n- ".join(failures))
        return 1
    print(f"Red-then-green check passed for {len(pairs)} feat commits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
