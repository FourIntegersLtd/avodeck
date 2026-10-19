"""How far each step of a case's workflow is (architecture 5.2 `GET /cases/{case_id}/steps/progress`).

A pack step names the things to count in its `meters` (workflow.yaml); this file knows how to count
each one, read-only, from the shared tables. A step with no meters, or a meter nobody knows, has no
countable unit and answers null. Counts only: never page text, names or other patient data.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from psycopg import AsyncConnection

Count = tuple[int, int]  # (done, total)

_ANSWERED = "('confirmed', 'edited')"


async def _one(conn: AsyncConnection, query: str, case_id: uuid.UUID) -> tuple[Any, ...]:
    cur = await conn.execute(query, {"case": case_id})
    row = await cur.fetchone()
    assert row is not None
    return row


async def pages_read(conn: AsyncConnection, case_id: uuid.UUID) -> Count:
    row = await _one(
        conn,
        """select (select count(distinct r.page_id) from app.page_reads r join app.pages p on p.id = r.page_id
                   where r.case_id = %(case)s and not p.quarantined),
                  (select count(*) from app.pages where case_id = %(case)s and not quarantined)""",
        case_id,
    )
    return int(row[0]), int(row[1])


async def questions_confirmed(conn: AsyncConnection, case_id: uuid.UUID) -> Count:
    row = await _one(
        conn,
        f"select count(*) filter (where status in {_ANSWERED}), count(*) from app.questions where case_id = %(case)s",  # noqa: S608
        case_id,
    )
    return int(row[0]), int(row[1])


async def facts_decided(conn: AsyncConnection, case_id: uuid.UUID) -> Count:
    row = await _one(
        conn,
        "select count(*) filter (where status <> 'suggested'), count(*) from app.facts where case_id = %(case)s",
        case_id,
    )
    return int(row[0]), int(row[1])


async def questions_with_opinion(conn: AsyncConnection, case_id: uuid.UUID) -> Count:
    row = await _one(
        conn,
        f"""select count(*) filter (where exists (select 1 from app.opinions o where o.question_id = q.id)), count(*)
            from app.questions q where q.case_id = %(case)s and q.status in {_ANSWERED}""",  # noqa: S608
        case_id,
    )
    return int(row[0]), int(row[1])


async def report_stages(conn: AsyncConnection, case_id: uuid.UUID) -> Count:
    """Of four stages, how many are reached: a report built, its final check passed, locked, served."""
    row = await _one(
        conn,
        """select (exists (select 1 from app.report_versions where case_id = %(case)s and status <> 'withdrawn'))::int
                + (exists (select 1 from app.final_check_runs where case_id = %(case)s and passed))::int
                + (exists (select 1 from app.report_versions
                           where case_id = %(case)s and status in ('locked', 'served')))::int
                + (exists (select 1 from app.report_versions where case_id = %(case)s and status = 'served'))::int""",
        case_id,
    )
    return int(row[0]), 4


METERS: dict[str, Callable[[AsyncConnection, uuid.UUID], Awaitable[Count]]] = {
    "pages_read": pages_read,
    "questions_confirmed": questions_confirmed,
    "facts_decided": facts_decided,
    "questions_with_opinion": questions_with_opinion,
    "report_stages": report_stages,
}


async def step_counts(conn: AsyncConnection, case_id: uuid.UUID, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One entry per pack step: `done`, `total` and `percent` (null when the step has nothing to count)."""
    out: list[dict[str, Any]] = []
    for step in steps:
        known = [METERS[m] for m in step.get("meters", []) if m in METERS]
        if not known:
            out.append({"key": step["key"], "done": None, "total": None, "percent": None})
            continue
        done = total = 0
        for meter in known:
            d, t = await meter(conn, case_id)
            done, total = done + d, total + t
        out.append(
            {"key": step["key"], "done": done, "total": total, "percent": round(100 * done / total) if total else None}
        )
    return out
