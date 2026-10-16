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
SLICE_RE = re.compile(r"(?:backend/app/slices|backend/tests|frontend/src/features|frontend/tests)/([a-z0-9_-]+)/")


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
    m = SLICE_RE.search(rel + "/")
    target = ["make", "test-slice", f"S={m.group(1)}"] if m else ["make", "test-core"]
    proc = subprocess.run(target, cwd=PROJECT, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stdout + proc.stderr).splitlines()[-40:])
        print(f"Tests failing after editing {rel}:\n{tail}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
