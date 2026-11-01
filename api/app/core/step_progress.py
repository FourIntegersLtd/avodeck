"""How far each step of a case's workflow is (architecture 5.2 `GET /cases/{case_id}/steps/progress`).

A pack step names the things to count in its `meters` (workflow.yaml); this file knows how to count
each one, read-only, from the shared tables. A step with no meters, or a meter nobody knows, has no
countable unit and answers null. Counts only: never page text, names or other patient data.

Every meter the steps name is counted in one statement, so the whole answer is one round trip to the
database however many steps and meters the pack has.
"""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

Count = tuple[int, int]  # (done, total)

_ANSWERED = "('confirmed', 'edited')"

# Each meter as two scalar SQL expressions over the case (`%(case)s`): (done, total).
METERS: dict[str, tuple[str, str]] = {
    "pages_read": (
        """(select count(distinct r.page_id) from app.page_reads r join app.pages p on p.id = r.page_id
            where r.case_id = %(case)s and not p.quarantined)""",
        "(select count(*) from app.pages where bundle_id = (select bundle_id from app.cases where id = %(case)s) and not quarantined)",
    ),
    "questions_confirmed": (
        f"(select count(*) from app.questions where case_id = %(case)s and status in {_ANSWERED})",
        "(select count(*) from app.questions where case_id = %(case)s)",
    ),
    "facts_decided": (
        "(select count(*) from app.facts where case_id = %(case)s and status <> 'suggested')",
        "(select count(*) from app.facts where case_id = %(case)s)",
    ),
    "questions_with_opinion": (
        f"""(select count(*) from app.questions q where q.case_id = %(case)s and q.status in {_ANSWERED}
             and exists (select 1 from app.opinions o where o.question_id = q.id))""",
        f"(select count(*) from app.questions where case_id = %(case)s and status in {_ANSWERED})",
    ),
    # Of four stages, how many are reached: a report built, its final check passed, locked, served.
    "report_stages": (
        """((exists (select 1 from app.report_versions where case_id = %(case)s and status <> 'withdrawn'))::int
            + (exists (select 1 from app.final_check_runs where case_id = %(case)s and passed))::int
            + (exists (select 1 from app.report_versions
                       where case_id = %(case)s and status in ('locked', 'served')))::int
            + (exists (select 1 from app.report_versions where case_id = %(case)s and status = 'served'))::int)""",
        "4",
    ),
}


async def meter_counts(conn: AsyncConnection, case_id: uuid.UUID, meters: list[str]) -> dict[str, Count]:
    """(done, total) for each known meter named, all counted in one statement."""
    known = sorted({m for m in meters if m in METERS})
    if not known:
        return {}
    columns = ", ".join(f"{METERS[m][0]}, {METERS[m][1]}" for m in known)
    cur = await conn.execute(f"select {columns}", {"case": case_id})  # noqa: S608 (fixed SQL above)
    row = await cur.fetchone()
    assert row is not None
    return {m: (int(row[2 * i]), int(row[2 * i + 1])) for i, m in enumerate(known)}


async def step_counts(conn: AsyncConnection, case_id: uuid.UUID, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per pack step: `done`, `total` and `percent` (null when the step has nothing to count)."""
    counts = await meter_counts(conn, case_id, [m for step in steps for m in step.get("meters", [])])
    out: list[dict[str, Any]] = []
    for step in steps:
        known = [counts[m] for m in step.get("meters", []) if m in counts]
        if not known:
            out.append({"key": step["key"], "done": None, "total": None, "percent": None})
            continue
        done, total = sum(d for d, _ in known), sum(t for _, t in known)
        out.append(
            {"key": step["key"], "done": done, "total": total, "percent": round(100 * done / total) if total else None}
        )
    return out
