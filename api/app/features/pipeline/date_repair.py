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
