#!/usr/bin/env python3
"""PreToolUse hook: during the 'implement' phase, block edits to test files.

Phase is set by the tdd-feature skill in .claude/phase (red | implement | refactor | idle).
Exit code 2 blocks the tool call and shows stderr to Claude.
"""
import json
import os
import sys
from pathlib import Path

PROJECT = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
PHASE_FILE = PROJECT / ".claude" / "phase"
PROTECTED_ALWAYS = ["backend/tests/core/", "frontend/tests/core/"]


def is_test_path(rel: str) -> bool:
    parts = rel.split("/")
    name = parts[-1]
    return (
        "tests" in parts
        or name.startswith("test_")
        or name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
        or ("/expected/" in rel and rel.startswith("evals/"))
    )


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    path = (data.get("tool_input") or {}).get("file_path")
    if not path:
        return 0
    try:
        rel = str(Path(path).resolve().relative_to(PROJECT))
    except ValueError:
        return 0

    phase = PHASE_FILE.read_text().strip() if PHASE_FILE.exists() else "idle"

    if any(rel.startswith(p) for p in PROTECTED_ALWAYS) and phase != "red":
        print(
            f"Blocked: {rel} is a non-negotiable core test. Change it only in the red phase, "
            "and only with the foundation owner's approval.",
            file=sys.stderr,
        )
        return 2

    if phase in ("implement", "refactor") and is_test_path(rel):
        print(
            f"Blocked: you are in the '{phase}' phase, so tests are frozen ({rel}). "
            "Make the code pass the existing tests. If a test is wrong, stop and explain why to the user.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
