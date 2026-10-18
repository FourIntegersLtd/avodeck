"""Which checks run, and what survives a re-run (docs/slices/D-understand.md, S13).

The checks are the pack's (`pack.checks()`), switched on or off per expert (`check_config`); a check
with `default_on: false` starts off. An expert may change a check's window, nothing else. A re-run
keeps the expert's decision where the same check rests on the same facts; anything else starts
undecided. Findings are never closed by code.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from app.features.understand.checks.rules import Hit

EXPERT_PARAMS = ("window_days",)


@dataclass(frozen=True)
class Decided:
    check_key: str
    evidence: frozenset[uuid.UUID]
    decision: str | None
    reason: str | None


@dataclass(frozen=True)
class Finding:
    hit: Hit
    decision: str | None
    reason: str | None


def configured_checks(
    pack_checks: Sequence[Mapping[str, Any]], config: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    out = []
    for check in pack_checks:
        mine = config.get(str(check["key"]), {})
        if not mine.get("enabled", check.get("default_on", True)):
            continue
        out.append({**check, **{k: mine[k] for k in EXPERT_PARAMS if k in mine}})
    return out


def carry_decisions(hits: Iterable[Hit], old: Iterable[Decided]) -> list[Finding]:
    left = {(d.check_key, d.evidence): d for d in old if d.decision is not None}
    out = []
    for hit in hits:
        kept = left.pop((hit.check_key, frozenset(hit.evidence)), None)
        out.append(Finding(hit, kept.decision if kept else None, kept.reason if kept else None))
    return out
