"""Re-derive a fact's event_date from printed words (architecture 6.4, F12).

Uses the same bar as ingest: a date the model points at that matches printed words in the pack's
order, or a single unambiguous date in the fact's cited words when there is no pointer. Never guesses
from free text outside those words.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any

from app.core.packs import MergedPack
from app.features.pipeline import dates


def resolve_fact_event_date(
    kind: str,
    fields: Mapping[str, Any],
    word_ids: Sequence[str],
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    pack: MergedPack,
    *,
    allow_century: bool = False,
    anchor: dt.date | None = None,
) -> tuple[dt.date | None, str | None]:
    ids = set(word_ids)
    cited = [(w, t) for w, t in ordered if w in ids and w not in struck]
    if not cited:
        return None, None
    field = dates.date_field(pack, kind)
    pointed = fields.get(field) if field else None
    page_found = dates.find([(w, t) for w, t in ordered if w not in struck], pack)
    when, basis = dates.event_date(pointed if pointed else None, page_found, pack)
    if when is not None and basis == dates.PRINTED:
        return when, basis
    cited_found = dates.find(cited, pack)
    when, basis = dates.event_date(pointed if pointed else None, cited_found, pack)
    if when is not None and basis == dates.PRINTED:
        return when, basis
    if not pointed or not str(pointed).strip():
        readable = {d.value for d in cited_found if not d.ambiguous}
        if len(readable) == 1 and len(cited_found) == 1:
            return readable.pop(), dates.PRINTED
    if allow_century:
        anchor = anchor or dt.date.today()
        if pointed and str(pointed).strip():
            when = dates.century_from_ambiguous_text(str(pointed), pack, anchor)
            if when is not None:
                return when, dates.PRINTED_CENTURY
        for _, text in cited:
            when = dates.century_from_ambiguous_text(text, pack, anchor)
            if when is not None:
                return when, dates.PRINTED_CENTURY
    return None, None


def line_above_date(
    kind: str,
    fields: Mapping[str, Any],
    word_ids: Sequence[str],
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    lines: Mapping[str, int],
    pack: MergedPack,
    *,
    anchor: dt.date | None = None,
) -> tuple[dt.date | None, str | None, list[str]]:
    """The date printed on the fact's own line, or the line immediately above it (never below).

    For a fact with a date field the model left empty. Returns the date, its basis and the date's
    word IDs, which the caller adds to the fact's citations so provenance covers the date."""
    field = dates.date_field(pack, kind)
    if field is None or str(fields.get(field) or "").strip():
        return None, None, []
    live = [(w, t) for w, t in ordered if w not in struck]
    position = {w: i for i, (w, _) in enumerate(live)}
    first = min((position[w] for w in word_ids if w in position), default=None)
    if first is None or live[first][0] not in lines:
        return None, None, []
    here = lines[live[first][0]]
    anchor = anchor or dt.date.today()
    nearest: tuple[int, dates.Printed] | None = None
    for printed in dates.find(live, pack):
        last = position[printed.word_ids[-1]]
        if last >= first or lines.get(printed.word_ids[-1]) not in (here, here - 1):
            continue
        if nearest is None or last > nearest[0]:
            nearest = (last, printed)
    if nearest is None:
        return None, None, []
    printed = nearest[1]
    when: dt.date | None = printed.value
    if printed.ambiguous:
        text = " ".join(t for w, t in live if w in printed.word_ids)
        when = dates.century_from_ambiguous_text(text, pack, anchor)
    if when is None:
        return None, None, []
    return when, dates.LINE_ABOVE, list(printed.word_ids)


def cited_with(word_ids: Sequence[str], extra: Sequence[str], ordered: Sequence[tuple[str, str]]) -> list[str]:
    """The run of words from the date's start to the fact's end, in reading order.

    Citations must be one unbroken run (provenance check), so the words between the date and the
    fact are cited too; the fact's own value is still found inside the run."""
    place = {w: i for i, (w, _) in enumerate(ordered)}
    known = [place[w] for w in (*word_ids, *extra) if w in place]
    if not known:
        return list(word_ids)
    return [w for w, _ in ordered[min(known) : max(known) + 1]]


def resolve_dated(
    kind: str,
    fields: Mapping[str, Any],
    word_ids: Sequence[str],
    ordered: Sequence[tuple[str, str]],
    struck: set[str],
    lines: Mapping[str, int],
    pack: MergedPack,
    *,
    anchor: dt.date,
    line_above: bool = True,
) -> tuple[dt.date | None, str | None, list[str]]:
    """A fact's date at ingest and in the repair job: printed, century-read, or from the line above.

    Returns (date, basis, extra word IDs to cite). With no date, basis is `please_check` when the
    model pointed at a date that could not be read, else None."""
    when, basis = resolve_fact_event_date(
        kind, fields, word_ids, ordered, struck, pack, allow_century=True, anchor=anchor
    )
    if when is not None:
        return when, basis, []
    if line_above:
        when, basis, extra = line_above_date(kind, fields, word_ids, ordered, struck, lines, pack, anchor=anchor)
        if when is not None:
            return when, basis, extra
    field = dates.date_field(pack, kind)
    pointed = fields.get(field) if field else None
    if pointed and str(pointed).strip():
        return None, dates.PLEASE_CHECK, []
    return None, None, []
