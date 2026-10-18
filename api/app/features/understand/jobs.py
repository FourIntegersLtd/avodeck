"""Slice D's background jobs: `suggest_links` (T-11) and `rerun_checks` (checks and T-13a).

Both are queued by the routes and run in the worker under the job's case (ADR 0013).

`rerun_checks` first works out when each fact reached the clinician, in code (`availability.py`), and
writes it with `app.set_fact_availability` (no new fact version). An entry that arrives with no mark is
unknown, never the date of the letter itself. No text reaches a log or a job row (rule 6).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from psycopg import AsyncConnection

from app.core.contracts import availability as evidence
from app.core.jobs import JobContext, registry
from app.core.packs import MergedPack
from app.features.understand import availability
from app.features.understand.repo import CHECKED
from app.features.understand.services import current

__all__ = ["CHECKED", "rerun_checks", "suggest_links"]


async def _case_pack(conn: AsyncConnection, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    assert row is not None
    return current().packs.combine(str(row[0]), str(row[1]), str(row[2]))


def _stamp(day: date | None) -> datetime | None:
    return None if day is None else datetime(day.year, day.month, day.day, tzinfo=UTC)


async def write_availability(conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack) -> None:
    """Work out and store `available_at` and its basis for every fact that is not rejected."""
    positions = await evidence.fact_positions(conn, case_id)
    own = sorted(pack.own_record_sources())
    cur = await conn.execute(
        "select f.id, f.page_id, f.kind, f.event_date, coalesce(p.source_type = any (%s), false),"
        " f.available_at, f.availability_basis from app.facts f join app.pages p on p.id = f.page_id"
        " where f.case_id = %s and f.status <> 'rejected'",
        (own, case_id),
    )
    rows = await cur.fetchall()
    placed = [availability.Placed(r[0], r[1], positions.get(r[0], 0), r[2], r[3], bool(r[4])) for r in rows]
    marks = [
        availability.Mark(e.page_id, e.kind, e.date, e.position)
        for e in await evidence.evidence_for_case(conn, case_id)
    ]
    cur = await conn.execute(
        "select fact_id, (available_at at time zone 'UTC')::date, unknown, created_at"
        " from app.availability_overrides where case_id = %s",
        (case_id,),
    )
    overrides = [availability.Override(r[0], r[1], bool(r[2]), r[3]) for r in await cur.fetchall()]
    worked = availability.compute(placed, marks, pack.arriving_kinds(), overrides)
    for fact_id, _, _, _, _, stored_at, stored_basis in rows:
        got = worked[fact_id]
        if (_stamp(got.available_at), got.basis) == (stored_at, stored_basis):
            continue
        await conn.execute(
            "select app.set_fact_availability(%s, %s, %s)", (fact_id, _stamp(got.available_at), got.basis)
        )


@registry.kind("suggest_links", interactive=False, changes_case=False)
async def suggest_links(ctx: JobContext) -> None:
    """Suggest links between checked facts and the confirmed questions; filled in by the T-11 slice step."""


@registry.kind("rerun_checks", interactive=False, changes_case=False)
async def rerun_checks(ctx: JobContext) -> None:
    """Work out availability, then run the case's checks; the checks arrive in the next slice step."""
    async with ctx.transaction() as conn:
        pack = await _case_pack(conn, ctx.case_id)
        await write_availability(conn, ctx.case_id, pack)
