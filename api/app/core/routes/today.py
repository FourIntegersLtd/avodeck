"""GET /v1/today (screen 03): the person's open case cards and the "needs you" items slices register.

Cases come through row-level security, so only cases the person can see appear; closed ones are
counted, deleted ones never show. Each provider runs in its own savepoint: one that fails is skipped
(its exception type is logged, never its message) and Today still loads. Items for cases not shown
are dropped. `organisation_id` narrows everything to one organisation; `q` narrows the cards (and so their
items and `open_count`) to labels containing it, ignoring case. The counts are the server's: the browser
only renders them.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Query, Request
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
    open_count: int  # open cases in this organisation, ignoring `q`


class Readiness(BaseModel):
    """The first-visit steps (account secured, profile filled in, signature saved) and how many are done."""

    account: bool
    profile: bool
    signature: bool
    done_steps: int
    total_steps: int


class Today(BaseModel):
    cases: list[CaseCard]
    needs_you: list[NeedsYouItem]
    organisations: list[TodayOrganisation]
    closed_count: int
    open_count: int  # open cases matching `organisation_id` and `q`
    due_this_month: int  # open cases due in the current month in the person's timezone
    readiness: Readiness


def _like_escape(q: str) -> str:
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _zone(name: str | None) -> str:
    """The person's timezone, or UTC when none is set or it is not a known zone."""
    try:
        return ZoneInfo(name).key if name else "UTC"
    except Exception:  # noqa: BLE001 - an unknown zone name must not stop Today
        return "UTC"


_CARDS = """
select c.id as case_id, c.label, c.organisation_id, o.name as organisation_name, o.kind as organisation_kind,
       c.status, c.stage, c.due_date, c.party_role, c.jurisdiction, c.case_type, c.specialty,
       coalesce(a.case_role, a.org_role) as your_role, c.on_hold
from app.cases c
left join app.organisations o on o.id = c.organisation_id
left join lateral app.case_access(c.id) a on true
where c.status = any(%(open)s) and (%(org)s::uuid is null or c.organisation_id = %(org)s::uuid)
  and (%(q)s::text is null or c.label ilike '%%' || %(q)s::text || '%%' escape '\\')
order by c.due_date nulls last, c.label, c.id
"""


@today_router.get("/today")
async def today(
    request: Request,
    conn: DbConn,
    organisation_id: uuid.UUID | None = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
) -> Today:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_CARDS, {"open": list(OPEN), "org": organisation_id, "q": _like_escape(q) if q else None})
        cards = [CaseCard(**row) for row in await cur.fetchall()]
        await cur.execute(
            "select count(*) as n from app.cases where status = any(%s) and (%s::uuid is null or organisation_id = %s)",
            (list(CLOSED), organisation_id, organisation_id),
        )
        closed = await cur.fetchone()
        await cur.execute(
            "select o.id, o.name, o.kind,"
            " (select count(*) from app.cases c where c.organisation_id = o.id and c.status = any(%s)) as open_count"
            " from app.organisations o where o.id = any(app.my_org_ids()) and o.closed_at is null"
            " order by o.kind, o.name, o.id",
            (list(OPEN),),
        )
        organisations = [TodayOrganisation(**row) for row in await cur.fetchall()]
        await cur.execute("select timezone from app.people where id = app.current_person_id()")
        person = await cur.fetchone()
        await cur.execute(
            "select count(*) as n from app.cases where status = any(%s) and due_date is not null"
            " and (%s::uuid is null or organisation_id = %s)"
            " and date_trunc('month', due_date) = date_trunc('month', (now() at time zone %s)::date)",
            (list(OPEN), organisation_id, organisation_id, _zone(person["timezone"] if person else None)),
        )
        due = await cur.fetchone()
        await cur.execute(
            "select exists (select 1 from app.experts where person_id = app.current_person_id()"
            " and nullif(profession, '') is not null and nullif(registration_number, '') is not null"
            " and cardinality(specialties) > 0) as profile,"
            " exists (select 1 from app.signatures where expert_id = app.current_person_id()) as signature"
        )
        steps = await cur.fetchone()
    flags = [
        bool(request.state.identity.mfa_enrolled),
        bool(steps and steps["profile"]),
        bool(steps and steps["signature"]),
    ]
    readiness = Readiness(
        account=flags[0], profile=flags[1], signature=flags[2], done_steps=sum(flags), total_steps=len(flags)
    )
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
    return Today(
        cases=cards,
        needs_you=items,
        organisations=organisations,
        closed_count=closed["n"] if closed else 0,
        open_count=len(cards),
        due_this_month=due["n"] if due else 0,
        readiness=readiness,
    )
