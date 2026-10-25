"""Screen 04: details suggested from the letter of instruction (architecture 5.2 and 6.3 T-04, slice C brief).

`POST /cases/{id}/prefill` queues `prefill_case` (202 + job). Suggestions wait for the expert: accepting
or dismissing records the choice once, and nothing is applied to the case here; screen 04 fills its form
from what was accepted, and the case changes only when the expert saves that form.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Query
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.http import DbConn
from app.core.jobs import registry
from app.features.setup.ai_tasks import PREFILL_FIELDS
from app.features.setup.common import not_found
from app.features.setup.jobs import PREFILL_VALUE
from app.features.setup.router import router
from app.features.setup.routes_case import Crypto, EditSetup, ViewContent, conflict

Field_ = Literal["instructing_party_name", "instructing_party_type", "party_role", "due_date", "court_track"]


class PrefillStarted(BaseModel):
    job_id: uuid.UUID


class Suggestion(BaseModel):
    id: uuid.UUID
    instruction_version_id: uuid.UUID
    field: Field_
    value: str = Field(description="Words from the letter, a pack key or a date (YYYY-MM-DD).")
    word_ids: list[str]
    status: Literal["suggested", "accepted", "dismissed"]
    decided_at: datetime | None


class Prefill(BaseModel):
    suggestions: list[Suggestion]
    reading: bool = Field(description="The letter is still being read for suggestions.")
    rejected: int = Field(description="Suggestions refused by the last run because the letter did not say them.")


class DecisionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Literal["accepted", "dismissed"]


_COLUMNS = "id, instruction_version_id, field, value, word_ids, status, decided_at"


async def _suggestion(conn: DbConn, crypto: Any, case_id: uuid.UUID, row: dict[str, Any]) -> Suggestion:
    value = (await crypto.decrypt(conn, case_id, bytes(row["value"]), purpose=PREFILL_VALUE)).decode()
    return Suggestion(**{**row, "value": value})


@router.post("/cases/{case_id}/prefill", status_code=202, response_model=PrefillStarted)
async def start_prefill(case_id: uuid.UUID, access: EditSetup, conn: DbConn) -> PrefillStarted:
    cur = await conn.execute(
        "select 1 from app.instruction_versions where case_id = %s and kind in ('letter', 'supplementary')"
        " and document_id is not null limit 1",
        (case_id,),
    )
    if await cur.fetchone() is None:
        raise conflict("Add the letter of instruction first.")
    return PrefillStarted(job_id=await registry.enqueue(conn, case_id, "prefill_case"))


@router.get("/cases/{case_id}/prefill", response_model=Prefill)
async def get_prefill(
    case_id: uuid.UUID,
    access: ViewContent,
    conn: DbConn,
    crypto: Crypto,
    open: Annotated[bool, Query(description="Only the suggestions still waiting for the expert.")] = False,  # noqa: A002
) -> Prefill:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_COLUMNS} from app.prefill_suggestions where case_id = %s"  # noqa: S608
            " and (not %s or status = 'suggested') order by array_position(%s::text[], field), created_at, id",
            (case_id, open, list(PREFILL_FIELDS)),
        )
        rows = await cur.fetchall()
    cur2 = await conn.execute(
        "select (select rejected from app.prefill_runs where case_id = %s order by created_at desc limit 1),"
        " exists (select 1 from app.jobs j where j.case_id = %s and j.kind = 'prefill_case'"
        " and j.status in ('queued', 'running')"
        " and not exists (select 1 from app.prefill_runs r where r.job_id = j.id))",
        (case_id, case_id),
    )
    state = await cur2.fetchone()
    assert state is not None
    return Prefill(
        suggestions=[await _suggestion(conn, crypto, case_id, r) for r in rows],
        reading=bool(state[1]),
        rejected=int(state[0] or 0),
    )


@router.patch("/cases/{case_id}/prefill/{suggestion_id}", response_model=Suggestion)
async def decide_prefill(
    case_id: uuid.UUID, suggestion_id: uuid.UUID, body: DecisionIn, access: EditSetup, conn: DbConn, crypto: Crypto
) -> Suggestion:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select status from app.prefill_suggestions where id = %s and case_id = %s for update",
            (suggestion_id, case_id),
        )
        found = await cur.fetchone()
        if found is None:
            raise not_found()
        if found["status"] != "suggested":
            raise conflict("This suggestion has already been decided.")
        await cur.execute(
            f"update app.prefill_suggestions set status = %s where id = %s returning {_COLUMNS}",  # noqa: S608
            (body.decision, suggestion_id),
        )
        row = await cur.fetchone()
    assert row is not None
    await audit.record(conn, action=f"case.prefill_{body.decision}", target=f"case:{case_id}", case_id=case_id)
    return await _suggestion(conn, crypto, case_id, row)
