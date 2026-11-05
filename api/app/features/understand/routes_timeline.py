"""Screen 11: the timeline (architecture 5.2 "11 Timeline", slice D brief).

The lanes, their order and their labels come from the case's pack (`pack.lanes()`,
`vocabulary.lanes`). Each event carries its status and the questions it is linked to. Filters: date
range, status and linked question. Suggestions on pages the person has not read stay hidden (rule 3).
"""

from __future__ import annotations

import base64
import json
import uuid
from collections import Counter
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, Query, Request

from app.core.auth.access import CaseAccess, require_case_access
from app.core.errors import ApiError
from app.core.http import DbConn
from app.core.packs import MergedPack
from app.features.understand import gaps, guidelines, overview, repo, series
from app.features.understand.common import case_pack
from app.features.understand.lanes import arrange, subtitle
from app.features.understand.router import router
from app.features.understand.timeline_models import (
    ChronologyRef,
    Event,
    Gap,
    Lane,
    Overview,
    Range,
    Timeline,
    TimelineCounts,
    TimelineSource,
)

View = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
Status = Literal["suggested", "verified", "edited"]
Only = Literal["not_linked", "things_to_look"]


def _rank(fact: repo.SeenFact, flagged: set[uuid.UUID], links: dict[uuid.UUID, list[uuid.UUID]]) -> int:
    if fact.fact_id in flagged:
        return 0
    if links.get(fact.fact_id):
        return 1
    return 3 if fact.status in repo.CHECKED else 2


PAGE_AFTER_CURSOR = 200
"""The page size when a cursor comes with no `limit`."""


def _encode(key: tuple[date, int, date, uuid.UUID]) -> str:
    raw = json.dumps([key[0].isoformat(), key[1], key[2].isoformat(), str(key[3])])
    return base64.urlsafe_b64encode(raw.encode()).decode().rstrip("=")


def _decode(cursor: str) -> repo.After:
    try:
        day, page_no, own, fact_id = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        return repo.After(date.fromisoformat(day), int(page_no), date.fromisoformat(own), uuid.UUID(fact_id))
    except (ValueError, TypeError, KeyError):
        raise ApiError("invalid", "That place in the list is not valid.", 422) from None


async def _gaps(
    request: Request,
    conn: DbConn,
    case_id: uuid.UUID,
    pack: MergedPack,
    shown: list[repo.SeenFact],
    as_seen_on: date | None,
) -> list[Gap]:
    """Gaps for the open things to look at; the whole dated record is read only when there is one."""
    found = await gaps.open_findings(conn, case_id)
    if not found:
        return []
    arrived = await repo.visible_facts(request, conn, case_id, repo.Filters(as_seen_on=as_seen_on), dated_only=True)
    return await gaps.assemble(conn, case_id, pack, found, shown, arrived)


@router.get("/cases/{case_id}/timeline", response_model=Timeline)
async def timeline(
    case_id: uuid.UUID,
    access: View,
    request: Request,
    conn: DbConn,
    since: Annotated[date | None, Query(alias="from")] = None,
    until: Annotated[date | None, Query(alias="to")] = None,
    status: Status | None = None,
    question_id: uuid.UUID | None = None,
    only: Annotated[
        Only | None, Query(description="Only entries not linked to a question, or with a thing to look at.")
    ] = None,
    as_seen_on: Annotated[date | None, Query(description="Only what had arrived by this day.")] = None,
    limit: Annotated[
        int | None,
        Query(ge=1, le=500, description="A page of entries in date order; leave out for the whole list."),
    ] = None,
    cursor: Annotated[str | None, Query(description="`next_cursor` of the page before.")] = None,
) -> Timeline:
    """With `limit`, the entries come a page at a time (lanes, list and unplaced hold the page's entries only).

    Counts, range and filter options always cover the whole record; series bars and gaps are worked out from
    the entries on the page.
    """
    pack = await case_pack(request, conn, case_id)
    after = _decode(cursor) if cursor else None
    if limit is None and after is not None:
        limit = PAGE_AFTER_CURSOR
    filters = repo.Filters(since, until, status, question_id, only, as_seen_on)
    facts = await repo.visible_facts(
        request, conn, case_id, filters, limit=None if limit is None else limit + 1, after=after
    )
    next_cursor = None
    if limit is not None and len(facts) > limit:
        facts = facts[:limit]
        next_cursor = _encode(repo.chrono_key(facts[-1]))
    if limit is not None:  # lanes list their entries by the date the record shows, undated last
        facts.sort(key=lambda f: (f.event_date is None, f.event_date or date.min, f.page_no, f.fact_id))
    totals = await repo.totals(request, conn, case_id)
    links = await repo.linked_questions(conn, case_id, [f.fact_id for f in facts])
    flagged = await repo.flagged_facts(conn, case_id)

    def event(fact: repo.SeenFact, series_key: str | None = None) -> Event:
        rank = _rank(fact, flagged, links)
        return Event(
            fact_id=fact.fact_id,
            kind=fact.kind,
            page_no=fact.page_no,
            event_date=fact.event_date,
            available_at=fact.available_at,
            status=fact.status,
            question_ids=links.get(fact.fact_id, []),
            has_finding=fact.fact_id in flagged,
            summary=repo.summary_of(fact),
            source=fact.source,
            date_basis=fact.date_basis,
            first_word_n=fact.first_word_n,
            series_key=series_key,
            rank=rank,
            featured=rank <= 1,
        )

    lanes = pack.lanes()
    placed, unplaced = arrange(facts, lanes, kind=lambda f: f.kind, fields=lambda f: f.fields)
    labels = pack.vocabulary.get("lanes", {})
    source_words = pack.vocabulary.get("source_types", {})
    counts = TimelineCounts(
        checked=totals.checked,
        please_check=totals.please_check,
        things_to_look=totals.things_to_look,
        not_linked=totals.not_linked,
        undated=totals.undated,
        total=totals.total,
    )
    icon_of = {str(lane["key"]): lane.get("icon") for lane in lanes}
    colour_of = {str(src["key"]): src.get("colour") for src in pack.source_types()}

    def lane_colour(got: list[repo.SeenFact]) -> str | None:
        tally = Counter(f.source for f in got if f.source in colour_of)
        return colour_of[tally.most_common(1)[0][0]] if tally else None

    kept = [f for got in placed for f in got[1]]
    lane_series = {key: series.build(pack, key, got) for key, got in placed}
    rows = [(key, f) for key, got in placed for f in got] + [("", f) for f in unplaced]
    rows.sort(key=lambda r: repo.chrono_key(r[1]))
    options = [
        value
        for value, n in (
            ("verified", counts.checked),
            ("suggested", counts.please_check),
            ("things_to_look", counts.things_to_look),
            ("not_linked", counts.not_linked),
        )
        if n > 0 or value == status
    ]
    return Timeline(
        chronology=[
            ChronologyRef(
                fact_id=f.fact_id,
                lane_key=key,
                lane_label=str(labels.get(key, key)) if key else "",
                lane_icon=icon_of.get(key),
            )
            for key, f in rows
        ],
        status_options=options,
        range=Range(first=totals.first, last=totals.last) if totals.first and totals.last else None,
        as_seen_on=as_seen_on,
        as_seen_title=pack.term("as_seen_title"),
        sources=[
            TimelineSource(key=str(s["key"]), label=str(source_words.get(s["key"], s["key"])), colour=s.get("colour"))
            for s in pack.source_types()
        ],
        counts=counts,
        lanes=[
            Lane(
                key=key,
                label=str(labels.get(key, key)),
                events=[event(f, lane_series[key].keys.get(f.fact_id)) for f in got],
                colour=lane_colour(got),
                icon=icon_of.get(key),
                subtitle=subtitle(pack.vocabulary, key, len(got)),
                series=lane_series[key].bars,
            )
            for key, got in placed
        ],
        gaps=await _gaps(request, conn, case_id, pack, kept, as_seen_on),
        unplaced=[event(f) for f in unplaced],
        guidance_changed=await guidelines.changed(conn, case_id, pack.guidelines()),
        next_cursor=next_cursor,
    )


@router.get("/cases/{case_id}/timeline/overview", response_model=Overview)
async def timeline_overview(
    case_id: uuid.UUID,
    access: View,
    request: Request,
    conn: DbConn,
    as_seen_on: Annotated[date | None, Query(description="Only what had arrived by this day.")] = None,
    since: Annotated[date | None, Query(alias="from", description="Count only from this day.")] = None,
    until: Annotated[date | None, Query(alias="to", description="Count only up to this day.")] = None,
) -> Overview:
    """The whole record for the ribbon: counts per lane per month and the entries worth a pin."""
    pack = await case_pack(request, conn, case_id)
    return await overview.build(request, conn, case_id, pack, as_seen_on, since, until)
