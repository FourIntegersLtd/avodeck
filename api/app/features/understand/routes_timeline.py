"""Screen 11: the timeline (architecture 5.2 "11 Timeline", slice D brief).

The lanes, their order and their labels come from the case's pack (`pack.lanes()`,
`vocabulary.lanes`). Each event carries its status and the questions it is linked to. Filters: date
range, status and linked question. Suggestions on pages the person has not read stay hidden (rule 3).
"""

from __future__ import annotations

import uuid
from collections import Counter
from datetime import date
from typing import Annotated, Literal

from fastapi import Depends, Query, Request

from app.core.auth.access import CaseAccess, require_case_access
from app.core.http import DbConn
from app.features.understand import gaps, guidelines, overview, repo, series
from app.features.understand.common import case_pack
from app.features.understand.lanes import arrange, subtitle
from app.features.understand.router import router
from app.features.understand.timeline_models import (
    Event,
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
) -> Timeline:
    pack = await case_pack(request, conn, case_id)
    facts = await repo.visible_facts(request, conn, case_id)
    links = await repo.linked_questions(conn, case_id)
    flagged = await repo.flagged_facts(conn, case_id)

    def keep(fact: repo.SeenFact) -> bool:
        if since is not None and (fact.event_date is None or fact.event_date < since):
            return False
        if until is not None and (fact.event_date is None or fact.event_date > until):
            return False
        if status == "verified" and fact.status not in repo.CHECKED:
            return False
        if status not in (None, "verified") and fact.status != status:
            return False
        if as_seen_on is not None and (fact.available_at is None or fact.available_at > as_seen_on):
            return False
        if only == "not_linked" and links.get(fact.fact_id):
            return False
        if only == "things_to_look" and fact.fact_id not in flagged:
            return False
        return question_id is None or question_id in links.get(fact.fact_id, [])

    def event(fact: repo.SeenFact, series_key: str | None = None) -> Event:
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
            series_key=series_key,
        )

    lanes = pack.lanes()
    placed, unplaced = arrange([f for f in facts if keep(f)], lanes, kind=lambda f: f.kind, fields=lambda f: f.fields)
    labels = pack.vocabulary.get("lanes", {})
    source_words = pack.vocabulary.get("source_types", {})
    counts = TimelineCounts(
        checked=sum(1 for f in facts if f.status in repo.CHECKED),
        please_check=sum(1 for f in facts if f.status not in repo.CHECKED),
        things_to_look=sum(1 for f in facts if f.fact_id in flagged),
        not_linked=sum(1 for f in facts if not links.get(f.fact_id)),
        undated=sum(1 for f in facts if f.event_date is None),
        total=len(facts),
    )
    icon_of = {str(lane["key"]): lane.get("icon") for lane in lanes}
    colour_of = {str(src["key"]): src.get("colour") for src in pack.source_types()}

    def lane_colour(got: list[repo.SeenFact]) -> str | None:
        tally = Counter(f.source for f in got if f.source in colour_of)
        return colour_of[tally.most_common(1)[0][0]] if tally else None

    days = [f.event_date for f in facts if f.event_date is not None]
    kept = [f for got in placed for f in got[1]]
    lane_series = {key: series.build(pack, key, got) for key, got in placed}
    return Timeline(
        range=Range(first=min(days), last=max(days)) if days else None,
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
        gaps=await gaps.build(conn, case_id, pack, kept, facts),
        unplaced=[event(f) for f in unplaced],
        guidance_changed=await guidelines.changed(conn, case_id, pack.guidelines()),
    )


@router.get("/cases/{case_id}/timeline/overview", response_model=Overview)
async def timeline_overview(
    case_id: uuid.UUID,
    access: View,
    request: Request,
    conn: DbConn,
    as_seen_on: Annotated[date | None, Query(description="Only what had arrived by this day.")] = None,
) -> Overview:
    """The whole record for the ribbon: counts per lane per month and the entries worth a pin."""
    pack = await case_pack(request, conn, case_id)
    return await overview.build(request, conn, case_id, pack, as_seen_on)
