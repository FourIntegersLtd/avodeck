#!/usr/bin/env python3
"""CI check: every feat commit must follow a test commit whose new tests failed before.

For each commit in BASE..HEAD (oldest first):
  - a 'feat(...)' commit must be immediately preceded by a 'test(...)' commit;
  - the test files changed in that test commit must FAIL at the test commit
    and PASS at the feat commit.
Backend test files run with pytest; frontend test files with vitest.
Usage: check_red_green.py <base_ref>
"""
import subprocess
import sys
import tempfile

TEST_PREFIXES = ("backend/tests/", "frontend/tests/")


def git(*args: str, cwd: str | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def changed_tests(sha: str) -> list[str]:
    files = git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines()
    return [f for f in files if f.startswith(TEST_PREFIXES) and not f.endswith((".json", ".md"))]


def run_tests(sha: str, files: list[str]) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        git("worktree", "add", "--detach", tmp, sha)
        try:
            ok = True
            py = [f for f in files if f.startswith("backend/") and f.endswith(".py")]
            ts = [f for f in files if f.startswith("frontend/")]
            if py:
                ok &= subprocess.run(["python", "-m", "pytest", "-q", *[f.removeprefix("backend/") for f in py]],
                                     cwd=f"{tmp}/backend").returncode == 0
            if ts:
                unit = [f.removeprefix("frontend/") for f in ts if "/e2e/" not in f]
                if unit:
                    subprocess.run(["pnpm", "install", "--frozen-lockfile"], cwd=f"{tmp}/frontend", check=True)
                    ok &= subprocess.run(["pnpm", "vitest", "run", *unit], cwd=f"{tmp}/frontend").returncode == 0
            return ok
        finally:
            git("worktree", "remove", "--force", tmp)


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "origin/main"
    shas = git("rev-list", "--reverse", f"{base}..HEAD").splitlines()
    msgs = {s: git("log", "-1", "--format=%s", s) for s in shas}
    failures = []
    for i, sha in enumerate(shas):
        if not msgs[sha].startswith("feat"):
            continue
        prev = shas[i - 1] if i > 0 else None
        if not prev or not msgs[prev].startswith("test"):
            failures.append(f"{sha[:8]} '{msgs[sha]}' is not preceded by a test commit")
            continue
        tests = changed_tests(prev)
        if not tests:
            failures.append(f"{prev[:8]} '{msgs[prev]}' adds no test files")
            continue
        if run_tests(prev, tests):
            failures.append(f"{prev[:8]} tests already passed before the feature (not red)")
        if not run_tests(sha, tests):
            failures.append(f"{sha[:8]} tests from {prev[:8]} do not pass (not green)")
    if failures:
        print("Red-then-green check failed:\n- " + "\n- ".join(failures))
        return 1
    print(f"Red-then-green check passed for {len(shas)} commits.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
