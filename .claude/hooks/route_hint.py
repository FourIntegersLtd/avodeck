#!/usr/bin/env python3
"""UserPromptSubmit hook: suggest which slice owner agent fits the prompt.

Hint only, never blocks. Reads .claude/routes.json. Silent on any error, on no
clear winner, or when ROUTE_HINT_OFF=1.
"""
import json
import os
import re
import sys
from pathlib import Path

PROJECT = Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()
ROUTES = PROJECT / ".claude" / "routes.json"


def score(prompt: str, routes: dict) -> list:
    text = prompt.lower().replace("\u2019", "'")
    screens = {"s" + n + b.lower() for n, b in re.findall(r"\bs(\d{2})(b?)(?![a-z0-9])", text)}
    for n, b in re.findall(r"\bscreen\s+(\d{1,2})\s?(b?)(?![a-z0-9])", text):
        screens.add("s" + n.zfill(2) + b)
    for n in re.findall(r"\bboard\s+(\d{2})\b", text):
        alias = routes.get("board_aliases", {}).get(n)
        if alias:
            screens.add(alias.lower())
    result = []
    for name, o in routes["owners"].items():
        points = 3 * len({s for s in o["screens"] if s.lower() in screens})
        for kw in o["keywords"]:
            if re.search(r"(?<![\w])" + re.escape(kw.lower()) + r"(?![\w])", text):
                points += 2 if " " in kw else 1
        result.append((points, name))
    return sorted(result, reverse=True)


def main() -> int:
    if os.environ.get("ROUTE_HINT_OFF") == "1":
        return 0
    try:
        prompt = json.load(sys.stdin).get("prompt") or ""
        routes = json.loads(ROUTES.read_text())
        ranked = score(prompt, routes)
        top = ranked[0]
        if top[0] < 2 or (len(ranked) > 1 and ranked[1][0] == top[0]):
            return 0
        o = routes["owners"][top[1]]
        msg = (
            f"Route: {top[1]} (slice {o['slice']}, {o['area']}). Brief: {o['brief']}. "
            f"Hand slice work to the {top[1]} agent, or follow its brief; "
            "paths it owns are in .claude/OWNERS.md."
        )
        print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": msg}}))
    except Exception:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
