"""Which pages a report may stand on, and how complete the records behind it are (security audit 16, PV-14, PV-15).

A page is *set aside* when the expert confirmed it as a copy, it was quarantined, or another page replaced it. A page
is *untyped* when it cannot be read by the computer (unreadable, or its reading failed) and the expert has typed
nothing in its place. A citation to either kind of page is not rendered (`[?]`) and fails the final check, which
names the page, in the opinions and in the expert's own sections alike.

Completeness is separate from citing: a report is still built when the records are incomplete, and the methodology
appendix says so. The final check blocks signing when pages cannot be read and are not typed, or processing of the
records is not finished (`all_pages_read`); a page shortfall is shown but does not block.

No page words or names reach here, only ids and page numbers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from psycopg import AsyncConnection

# A page is set aside as a copy only when the expert confirmed it: a copy only suggested is still read and counted.
SET_ASIDE = (
    "(p.quarantined or p.superseded_by_page_id is not null"
    " or (p.duplicate_of_page_id is not null and coalesce(a.duplicate_status, '') = 'confirmed'))"
)
UNTYPED = "((p.unreadable or a.read_failure is not null) and coalesce(length(p.transcribed_by_expert_text), 0) = 0)"
PROCESSING_KINDS = ("scan_document", "process_bundle", "process_pages", "reprocess_case")
# Work is still going while the newest job is queued or running, or paused with a code the worker resumes on its own
# (state-machines.md, Job). A failed job is final (a retry is the person's move) and a job paused for the cost guard or
# a cancel waits for a person: neither is "processing open", and pages left unread or unreadable are caught by the
# page gate in any case.
RESUMABLE_PAUSES = ("service_busy", "model_unavailable", "resuming", "busy_processing")


@dataclass(frozen=True)
class PageState:
    no: int
    set_aside: bool
    untyped: bool

    @property
    def usable(self) -> bool:
        return not self.set_aside and not self.untyped

    def fix(self) -> tuple[str, str]:
        """Where the expert deals with the page: the page list for one set aside, the reader for one to type."""
        return ("S08", str(self.no)) if self.set_aside else ("S10", str(self.no))


@dataclass(frozen=True)
class Completeness:
    untyped: list[int] = field(default_factory=list)
    processing_open: bool = False
    shortfall: int = 0  # files that said they had more pages than were found

    def codes(self) -> list[str]:
        found = []
        if self.untyped:
            found.append("unreadable_pages")
        if self.processing_open:
            found.append("processing_open")
        if self.shortfall:
            found.append("page_shortfall")
        return found


async def page_states(conn: AsyncConnection, case_id: uuid.UUID) -> dict[uuid.UUID, PageState]:
    cur = await conn.execute(
        f"select p.id, p.bundle_page_no, {SET_ASIDE}, {UNTYPED} from app.pages p"
        " left join app.page_analysis a on a.page_id = p.id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) order by p.bundle_page_no",
        (case_id,),
    )
    return {pid: PageState(int(no), bool(aside), bool(untyped)) for pid, no, aside, untyped in await cur.fetchall()}


def usable_pages(states: dict[uuid.UUID, PageState]) -> dict[uuid.UUID, int]:
    """Page number by page id, for the pages a citation may stand on."""
    return {pid: s.no for pid, s in states.items() if s.usable}


async def completeness(conn: AsyncConnection, case_id: uuid.UUID, states: dict[uuid.UUID, PageState]) -> Completeness:
    # The newest job of each processing kind: while the worker is still on it, the records may be incomplete.
    cur = await conn.execute(
        "select exists (select 1 from (select distinct on (kind) kind, status, error_code from app.jobs"
        " where case_id = %s and kind = any(%s) order by kind, created_at desc, id desc) j"
        " where j.status in ('queued', 'running') or (j.status = 'paused' and j.error_code = any(%s)))",
        (case_id, list(PROCESSING_KINDS), list(RESUMABLE_PAUSES)),
    )
    open_row = await cur.fetchone()
    cur = await conn.execute(
        "select count(*) from app.documents d"
        " where d.bundle_id = (select bundle_id from app.cases where id = %s)"
        " and d.pages_declared is not null and d.pages is not null and d.pages_declared > d.pages",
        (case_id,),
    )
    short = await cur.fetchone()
    return Completeness(
        untyped=sorted(s.no for s in states.values() if s.untyped and not s.set_aside),
        processing_open=bool(open_row and open_row[0]),
        shortfall=int(short[0]) if short else 0,
    )
