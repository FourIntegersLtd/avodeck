"""Gaps on the timeline (docs/design/timeline-v2-followups.md, item 4): for each open thing to look at, how long
it waited from its earliest shown evidence to the first later entry the check counts as action, and whether the
records simply go quiet there. Code decides all of it; the words are keys the web app turns into text.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from psycopg import AsyncConnection

from app.core.contracts import record_gaps
from app.core.packs import MergedPack
from app.features.understand import repo
from app.features.understand.timeline_models import Gap

ACTION_KEYS = ("action_kinds", "acknowledged_kinds", "followup_kinds", "or_kinds")
NONE_AFTER = "timeline.gap.none_after"
NO_ACTION_AFTER = "timeline.gap.no_action_after"
RECORDS_QUIET = "records_quiet"


@dataclass(frozen=True)
class Finding:
    id: uuid.UUID
    check_key: str
    rule: str
    evidence_fact_ids: list[uuid.UUID]


async def open_findings(conn: AsyncConnection, case_id: uuid.UUID) -> list[Finding]:
    cur = await conn.execute(
        "select id, check_key, rule, evidence_fact_ids from app.findings where case_id = %s and status = 'open'",
        (case_id,),
    )
    return [Finding(id=r[0], check_key=r[1], rule=r[2], evidence_fact_ids=list(r[3])) for r in await cur.fetchall()]


def _action_kinds(check: dict[str, Any] | None) -> set[str]:
    return {str(k) for key in ACTION_KEYS for k in (check or {}).get(key, [])}


async def assemble(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    pack: MergedPack,
    findings: Sequence[Finding],
    shown: Sequence[repo.SeenFact],
    visible: Sequence[repo.SeenFact],
) -> list[Gap]:
    checks = {str(c["key"]): c for c in pack.checks()}
    shown_by_id = {f.fact_id: f for f in shown if f.event_date is not None}
    dated = [f for f in visible if f.event_date is not None]
    latest = max((f.event_date for f in dated if f.event_date), default=None)
    out: list[tuple[date, int, Gap]] = []
    for found in findings:
        starts = [shown_by_id[i] for i in found.evidence_fact_ids if i in shown_by_id]
        if not starts:
            continue
        start = min(starts, key=lambda f: (f.event_date, f.page_no))
        begin = start.event_date
        assert begin is not None
        kinds = _action_kinds(checks.get(found.check_key))
        after = [f for f in dated if f.event_date and f.event_date > begin]
        later = [f for f in after if not kinds or f.kind in kinds]
        end_fact = min(later, key=lambda f: (f.event_date, f.page_no), default=None)
        end = end_fact.event_date if end_fact and end_fact.event_date else max(latest or begin, begin)
        quiet = [
            g
            for g in await record_gaps.gaps_overlapping(conn, case_id, begin, end)
            if g.source_type == start.source or (g.fact_kind is not None and g.fact_kind in kinds)
        ]
        kind = RECORDS_QUIET if quiet else found.rule
        if end_fact or (after and quiet):
            label = f"timeline.gap.{kind}"
        else:
            label = NO_ACTION_AFTER if after else NONE_AFTER
        out.append(
            (
                begin,
                start.page_no,
                Gap(
                    finding_id=found.id,
                    from_fact_id=start.fact_id,
                    to_fact_id=end_fact.fact_id if end_fact else None,
                    days=(end - begin).days,
                    kind=kind,
                    label_key=label,
                ),
            )
        )
    out.sort(key=lambda row: (row[0], row[1]))
    return [row[2] for row in out]


async def build(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    pack: MergedPack,
    shown: Sequence[repo.SeenFact],
    visible: Sequence[repo.SeenFact],
) -> list[Gap]:
    """Gaps starting at an entry in `shown` (the timeline's window); `visible` is everything the person may see."""
    return await assemble(conn, case_id, pack, await open_findings(conn, case_id), shown, visible)
