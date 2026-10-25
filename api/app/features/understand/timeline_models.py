"""Shapes of screen 11's replies (architecture 5.2 "11 Timeline"), shared by the timeline route and the
parts that fill it in (`gaps.py`, `series.py`, `overview.py`). Contracts only grow: fields are added with
defaults, never renamed or removed.
"""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel


class Event(BaseModel):
    fact_id: uuid.UUID
    kind: str
    page_no: int
    event_date: date | None
    available_at: date | None
    status: str
    question_ids: list[uuid.UUID]
    has_finding: bool
    summary: str | None = None
    source: str | None = None
    series_key: str | None = None
    date_basis: str | None = None
    first_word_n: int | None = None
    rank: int = 3
    """How much the entry matters, lower first: 0 a thing to look at, 1 linked to a question, 2 please check, 3 checked."""
    featured: bool = False
    """Worth a card even when the board is zoomed far out (a thing to look at or a question link)."""


class ChronologyRef(BaseModel):
    """One row of the list view: the entry (by id) and the lane it sits in, already in date order."""

    fact_id: uuid.UUID
    lane_key: str = ""
    lane_label: str = ""
    lane_icon: str | None = None


class Series(BaseModel):
    """Entries the lane's pack rule groups into one bar (e.g. one medicine issued again and again)."""

    key: str
    label: str
    first: date
    last: date
    count: int
    fact_ids: list[uuid.UUID]


class Lane(BaseModel):
    key: str
    label: str
    events: list[Event]
    colour: str | None = None
    icon: str | None = None
    subtitle: str | None = None
    series: list[Series] | None = None


class TimelineSource(BaseModel):
    key: str
    label: str
    colour: str | None


class TimelineCounts(BaseModel):
    """The numbers on the filter chips, counted over everything the person may see (before any filter)."""

    checked: int
    please_check: int
    things_to_look: int
    not_linked: int
    undated: int
    total: int


class Range(BaseModel):
    first: date
    last: date


class Gap(BaseModel):
    """The wait after an entry an open thing to look at rests on, in the server's own words (`label_key`)."""

    finding_id: uuid.UUID
    from_fact_id: uuid.UUID
    to_fact_id: uuid.UUID | None
    days: int
    kind: str
    label_key: str


class Timeline(BaseModel):
    lanes: list[Lane]
    unplaced: list[Event]
    sources: list[TimelineSource] = []
    counts: TimelineCounts | None = None
    range: Range | None = None
    as_seen_on: date | None = None
    as_seen_title: str | None = None
    guidance_changed: list[str] = []
    gaps: list[Gap] | None = None
    chronology: list[ChronologyRef] = []
    """Every entry shown, in date order (undated last, then by page)."""
    status_options: list[str] = []
    """The status filters worth offering: those with something in them, and the one chosen."""


class OverviewLane(BaseModel):
    key: str
    label: str
    colour: str | None = None
    counts: list[int]


class Pin(BaseModel):
    fact_id: uuid.UUID
    date: date
    lane_key: str
    has_finding: bool
    question_ids: list[uuid.UUID]


class Overview(BaseModel):
    """The ribbon: the whole record, counted per lane per month (`months` are "YYYY-MM")."""

    range: Range | None = None
    months: list[str] = []
    lanes: list[OverviewLane] = []
    pins: list[Pin] = []
