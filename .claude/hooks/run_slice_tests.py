#!/usr/bin/env python3
"""PostToolUse hook: in the implement/refactor phase, run the edited slice's tests.

Failing tests are reported back to Claude (exit 2) so it keeps working until green.
In the red phase, failures are expected, so nothing is run.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

PROJECT = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
PHASE_FILE = PROJECT / ".claude" / "phase"
# The feature folder (api/app/features/<f>/, web/src/capabilities/<f>/, web/e2e/<f>/) or test file (api/tests/<kind>/test_<f>_*.py).
DIR_RE = re.compile(r"^(?:api/app/features|web/src/capabilities|web/e2e)/([a-z0-9_-]+)/")
TEST_RE = re.compile(r"^api/tests/[^/]+/test_([a-z0-9_]+)\.py$")


def features() -> list[str]:
    routes = json.loads((PROJECT / ".claude" / "routes.json").read_text())
    names = {o["feature"] for o in routes["owners"].values()} - {"foundation"}
    return sorted(names, key=len, reverse=True)


def feature_for(rel: str) -> str | None:
    m = DIR_RE.match(rel)
    if m:
        name = m.group(1).replace("-", "_")
        return name if name in features() else None
    m = TEST_RE.match(rel)
    if m:
        return next((f for f in features() if m.group(1).startswith(f + "_")), None)
    return None


def main() -> int:
    phase = PHASE_FILE.read_text().strip() if PHASE_FILE.exists() else "idle"
    if phase not in ("implement", "refactor"):
        return 0
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    path = (data.get("tool_input") or {}).get("file_path") or ""
    try:
        rel = str(Path(path).resolve().relative_to(PROJECT))
    except ValueError:
        return 0
    feature = feature_for(rel)
    target = ["make", "test-slice", f"S={feature}"] if feature else ["make", "test-core"]
    proc = subprocess.run(target, cwd=PROJECT, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
        print(f"Tests failing after editing {rel}:\n{tail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
