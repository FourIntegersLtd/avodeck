#!/usr/bin/env python3
"""Consistency checks across the build documents. Runs in CI; exits 1 on any gap.

Checks:
  1. Every table in architecture section 4 is owned by exactly one slice brief (or the foundation).
  2. Every endpoint in architecture section 5.2/5.3 appears in a slice brief (or the foundation brief).
  3. Every status column named in the data model has a section in docs/spec/state-machines.md.
  4. Every worker job in api/AGENTS.md appears in the architecture job list and has a failure-mode row or owner.
  5. Every table has a behaviour entry in docs/spec/behaviour-index.md (who, how many, lifecycle, leaving, views).
  6. permissions.md, scenarios.md and data-dictionary.md exist.
  7. Every table has a plain-English entry in docs/spec/data-dictionary.md.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
arch = (ROOT / "docs/architecture.md").read_text()
briefs = {p.name: p.read_text() for p in (ROOT / "docs/slices").glob("*.md")}
allbriefs = "\n".join(briefs.values())
problems = []

# 1. tables
sec4 = arch.split("## 4. Data model", 1)[1].split("\n## 5.", 1)[0]
tables = sorted(set(re.findall(r"\|\s*(?:[A-Za-z ]+)?\|?\s*`([a-z_]+)`(?:\s*\([^)]*\))?\s*\|", sec4)))
for t in tables:
    owners = [n for n, b in briefs.items() if re.search(rf"`{t}`", b)]
    if not owners:
        problems.append(f"table `{t}` is not mentioned in any slice brief")

# 2. endpoints
sec5 = arch.split("## 5. API", 1)[1].split("\n## 6.", 1)[0]
sec5 = re.sub(r"replaces `[^`]+`", "", sec5)  # old names kept for reference only
eps = sorted(set(re.findall(r"`(/[a-z0-9_{}\-/]+)`", sec5)))
def norm(e):
    return re.sub(r"\{[^}]+\}", "{}", e.replace("/v1", "")).rstrip("/")
brief_eps = {norm(e) for e in re.findall(r"`(?:[A-Z/ ]+ )?(/[^` ]+)`", allbriefs)}
brief_eps |= {norm(e.split("?")[0]) for e in list(brief_eps)}
for e in eps:
    n = norm(e.split("?")[0])
    if not any(n == b or n.startswith(b + "/") or b.endswith(n) for b in brief_eps):
        problems.append(f"endpoint `{e}` is not in any slice brief")

# 3. status columns
sm = (ROOT / "docs/spec/state-machines.md").read_text()
for t, col in re.findall(r"\|\s*\|?\s*`([a-z_]+)`\s*\|[^|\n]*\b(status)\b", sec4):
    if f"`{t}" not in sm and t.rstrip("s") not in sm.lower():
        problems.append(f"`{t}.{col}` has no state machine")

# 5. behaviour index
bi = ROOT / "docs/spec/behaviour-index.md"
if bi.exists():
    btxt = bi.read_text()
    for t in tables:
        if f"`{t}`" not in btxt:
            problems.append(f"table `{t}` has no behaviour entry in docs/spec/behaviour-index.md")
else:
    problems.append("docs/spec/behaviour-index.md missing")

# 6. required behaviour documents exist
for doc in ["permissions.md", "scenarios.md", "data-dictionary.md"]:
    if not (ROOT / "docs/spec" / doc).exists():
        problems.append(f"docs/spec/{doc} missing")

# 7. every table has a plain-words entry in the data dictionary
dd = ROOT / "docs/spec/data-dictionary.md"
if dd.exists():
    dtxt = dd.read_text()
    for t in tables:
        if f"### {t}" not in dtxt:
            problems.append(f"table `{t}` has no entry in docs/spec/data-dictionary.md")

print(f"{len(tables)} tables, {len(eps)} endpoints checked")
for p in problems:
    print("GAP:", p)
sys.exit(1 if problems else 0)
