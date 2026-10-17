"""GET /v1/today (screen 03): the person's open case cards and the "needs you" items slices register.

Cases come through row-level security, so only cases the person can see appear; closed ones are
counted, deleted ones never show. Each provider runs in its own savepoint: one that fails is skipped
(its exception type is logged, never its message) and Today still loads. Items for cases not shown
are dropped. `organisation_id` narrows everything to one organisation.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import Request
from psycopg.rows import dict_row
from pydantic import BaseModel

from app.core.contracts.today import CaseCard, NeedsYouItem, NeedsYouRegistry
from app.core.http import DbConn, router

logger = logging.getLogger("app.today")
today_router = router(prefix="/v1", tags=["today"])

OPEN = ("draft", "processing", "active", "served")
CLOSED = ("settled", "withdrawn", "archived")
_LAST = datetime.max.replace(tzinfo=UTC)


class TodayOrganisation(BaseModel):
    id: uuid.UUID
    name: str
    kind: str


class Today(BaseModel):
    cases: list[CaseCard]
    needs_you: list[NeedsYouItem]
    organisations: list[TodayOrganisation]
    closed_count: int


_CARDS = """
select c.id as case_id, c.label, c.organisation_id, o.name as organisation_name, o.kind as organisation_kind,
       c.status, c.stage, c.due_date, c.party_role, c.jurisdiction, c.case_type, c.specialty,
       coalesce(a.case_role, a.org_role) as your_role, c.on_hold
from app.cases c
left join app.organisations o on o.id = c.organisation_id
left join lateral app.case_access(c.id) a on true
where c.status = any(%(open)s) and (%(org)s::uuid is null or c.organisation_id = %(org)s::uuid)
order by c.due_date nulls last, c.label, c.id
"""


@today_router.get("/today")
async def today(request: Request, conn: DbConn, organisation_id: uuid.UUID | None = None) -> Today:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_CARDS, {"open": list(OPEN), "org": organisation_id})
        cards = [CaseCard(**row) for row in await cur.fetchall()]
        await cur.execute(
            "select count(*) as n from app.cases where status = any(%s) and (%s::uuid is null or organisation_id = %s)",
            (list(CLOSED), organisation_id, organisation_id),
        )
        closed = await cur.fetchone()
        await cur.execute(
            "select id, name, kind from app.organisations where id = any(app.my_org_ids()) and closed_at is null"
            " order by kind, name, id"
        )
        organisations = [TodayOrganisation(**row) for row in await cur.fetchall()]
    registry: NeedsYouRegistry = request.app.state.needs_you
    shown = {card.case_id for card in cards}
    items: list[NeedsYouItem] = []
    if cards:
        for name, provider in registry.providers.items():
            try:
                async with conn.transaction():
                    found = await provider(conn, list(cards))
            except Exception as exc:  # noqa: BLE001 - one slice's failure must not stop Today
                logger.warning("needs-you provider %s failed: %s", name, type(exc).__name__)
                continue
            items.extend(i for i in found if isinstance(i, NeedsYouItem) and i.case_id in shown)
    items.sort(key=lambda i: (i.rank, i.due_at or _LAST))
    return Today(cases=cards, needs_you=items, organisations=organisations, closed_count=closed["n"] if closed else 0)
