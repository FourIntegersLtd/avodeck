"""Rule 3, blind read: suggestions for a page stay hidden until the person has marked it read.

Slice B owns the read log (`page_reads`) and the routes; the gate is core so every facts route
uses the same check. `GET /v1/cases/{case_id}/pages/{page_no}/facts` depends on
`require_page_read()`: 404/403 from the case access check first, then 423 `blind_read` until
`POST .../pages/{page_no}/read` is recorded for the person asking. With no read log the gate
stays shut (fail closed).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fastapi import Request
from psycopg import AsyncConnection

from app.core.auth.access import CaseAccess, check_case_access
from app.core.auth.permissions import CASE_ACTIONS
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
from app.core.errors import ApiError
from app.core.http import DbConn


class PageReadLog(Protocol):
    """Who has read which page. Slice B implements it on `page_reads`; `create_app(page_reads=...)`."""

    async def has_read(self, conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
        """Whether `person_id` has marked bundle page `page_no` of the case read."""
        ...

    async def read_page_ids(self, conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
        """The ids of the case's pages `person_id` has marked read."""
        ...


class PageReads:
    """`PageReadLog` on slice B's `page_reads`, for code with no app to ask (the worker).

    `has_read` is the person's own read, so an assistant who has read a page sees its suggestions.
    `read_page_ids` lists only reads that count for citing (the named expert's), so `citable_facts()`
    never unlocks a fact through anyone else's reading.
    """

    async def has_read(self, conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
        cur = await conn.execute(
            "select exists (select 1 from app.page_reads r join app.pages p on p.id = r.page_id"
            " where r.case_id = %s and p.bundle_page_no = %s and r.person_id = %s)",
            (case_id, page_no, person_id),
        )
        row = await cur.fetchone()
        return bool(row and row[0])

    async def read_page_ids(self, conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
        cur = await conn.execute(
            "select distinct page_id from app.page_reads where case_id = %s and person_id = %s and counts_for_citing",
            (case_id, person_id),
        )
        return {r[0] for r in await cur.fetchall()}


@dataclass(frozen=True)
class PageReadEntry:
    """One row of slice B's read log, as slice H's export and slice F's methodology appendix show it."""

    page_no: int
    person_id: uuid.UUID
    read_at: datetime
    dwell_seconds: int
    counts_for_citing: bool


async def page_read_log(conn: AsyncConnection, case_id: uuid.UUID) -> list[PageReadEntry]:
    """Every read of the case in read order (bundle page number, who, when, seconds, counts for citing).

    Runs on the caller's connection, so row-level security applies: no access, no reads."""
    cur = await conn.execute(
        "select p.bundle_page_no, r.person_id, r.at, r.dwell_seconds, r.counts_for_citing"
        " from app.page_reads r join app.pages p on p.id = r.page_id where r.case_id = %s order by r.seq",
        (case_id,),
    )
    return [
        PageReadEntry(int(n), person, at, int(dwell), bool(counts))
        for n, person, at, dwell, counts in await cur.fetchall()
    ]


async def verification_counts(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, int]:
    """How many checks of each kind the case's verification log holds (`fact_verifications.action`)."""
    cur = await conn.execute(
        "select action, count(*)::int from app.fact_verifications where case_id = %s group by action", (case_id,)
    )
    return {str(action): int(n) for action, n in await cur.fetchall()}


def blind_read() -> ApiError:
    return ApiError("blind_read", "Mark this page as read first.", 423)


class PageReadGate:
    """A FastAPI dependency. `case_action`, `step_up` and `page_read_gate` let the route scanners see it."""

    page_read_gate = True

    def __init__(self, action: str) -> None:
        if action not in CASE_ACTIONS:
            raise ValueError(f"unknown case action {action!r}: add it to docs/spec/permissions.md first")
        self.case_action = action
        self.step_up = CASE_ACTIONS[action].step_up

    async def __call__(self, request: Request, case_id: uuid.UUID, page_no: str, conn: DbConn) -> CaseAccess:
        # `page_no` arrives as text so the access check answers first: no hint the case exists.
        access = await check_case_access(conn, case_id, self.case_action)
        if not page_no.isdigit():
            raise ApiError("not_found", "We couldn't find that page.", 404)
        if self.step_up:
            check_step_up(current_session(request))
        reads: PageReadLog | None = getattr(request.app.state, "page_reads", None)
        person = request.state.scope.person_id
        if reads is None or not await reads.has_read(conn, case_id, int(page_no), person):
            raise blind_read()
        return access


def require_page_read(action: str = "page.read") -> PageReadGate:
    return PageReadGate(action)
