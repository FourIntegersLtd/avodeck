"""Resolve a fact's calendar date from printed OCR words (slice A implements; slice B calls on verify)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.core.contracts.pipeline import EntryPoint
from app.core.packs import MergedPack

DATE_BASIS_PRINTED = "printed"


class ResolveFactEventDate(Protocol):
    def __call__(
        self,
        kind: str,
        fields: Mapping[str, Any],
        word_ids: Sequence[str],
        ordered: Sequence[tuple[str, str]],
        struck: set[str],
        pack: MergedPack,
        *,
        allow_century: bool = False,
        anchor: dt.date | None = None,
    ) -> tuple[dt.date | None, str | None]: ...


_resolve_entry: EntryPoint[ResolveFactEventDate] = EntryPoint("resolve_fact_event_date")


def register_resolve_fact_event_date(fn: ResolveFactEventDate) -> ResolveFactEventDate:
    return _resolve_entry.register(fn)


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
    return _resolve_entry.get()(
        kind,
        fields,
        word_ids,
        ordered,
        struck,
        pack,
        allow_century=allow_century,
        anchor=anchor,
    )
