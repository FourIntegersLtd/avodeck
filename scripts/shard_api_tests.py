#!/usr/bin/env python3
"""Deterministic split of the API test files into N shards, balanced by run time.

Usage:  python scripts/shard_api_tests.py <shard 1..N> <N>             prints that shard's test files
        python scripts/shard_api_tests.py --durations <junit.xml>...   prints a durations JSON from JUnit reports

Files are the unit (a file is never split, so no test can run twice or be skipped). Greedy
longest-first onto the lightest shard, using api/.test_durations.json (file -> seconds). A file
missing from that JSON is still assigned: its weight is its line count scaled to seconds, so a
new test file always runs somewhere. (pytest-split cannot be used: it needs pytest < 9.)
"""

from __future__ import annotations

import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path

API = Path(__file__).resolve().parent.parent / "api"
DURATIONS = API / ".test_durations.json"


def test_files() -> list[str]:
    return sorted(p.relative_to(API).as_posix() for p in (API / "tests").rglob("test_*.py"))


def weights(files: list[str]) -> dict[str, float]:
    known: dict[str, float] = json.loads(DURATIONS.read_text()) if DURATIONS.exists() else {}
    lines = {f: max(1, len((API / f).read_text().splitlines())) for f in files}
    both = [f for f in files if f in known]
    scale = sum(known[f] for f in both) / sum(lines[f] for f in both) if both else 0.05
    return {f: known[f] if f in known else lines[f] * scale for f in files}


def shard(index: int, count: int) -> list[str]:
    files = test_files()
    w = weights(files)
    loads = [0.0] * count
    out: list[list[str]] = [[] for _ in range(count)]
    for f in sorted(files, key=lambda f: (-w[f], f)):
        i = min(range(count), key=lambda i: (loads[i], i))
        loads[i] += w[f]
        out[i].append(f)
    return sorted(out[index - 1])


def durations_from_junit(paths: list[str]) -> dict[str, float]:
    total: dict[str, float] = defaultdict(float)
    for p in paths:
        for case in ET.parse(p).getroot().iter("testcase"):
            parts = case.get("classname", "").split(".")
            # classname is a dotted module path, optionally followed by a class name
            while parts and not (API / "/".join(parts)).with_suffix(".py").exists():
                parts.pop()
            if parts:
                total[Path("/".join(parts)).with_suffix(".py").as_posix()] += float(case.get("time", 0))
    return {k: round(v, 2) for k, v in sorted(total.items())}


if __name__ == "__main__":
    if sys.argv[1] == "--durations":
        print(json.dumps(durations_from_junit(sys.argv[2:]), indent=1))
    else:
        print("\n".join(shard(int(sys.argv[1]), int(sys.argv[2]))))
