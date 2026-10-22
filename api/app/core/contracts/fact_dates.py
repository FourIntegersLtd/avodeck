"""Resolve a fact's calendar date from printed OCR words (slice A implements; slice B calls on verify)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from psycopg import AsyncConnection

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


async def latest_printed_date(conn: AsyncConnection, case_id: uuid.UUID) -> dt.date | None:
    """The latest date printed in full on any page of the case read so far (never after today)."""
    cur = await conn.execute(
        "select max(d) from app.page_analysis a, unnest(a.printed_dates) d where a.case_id = %s and d <= %s",
        (case_id, dt.date.today()),
    )
    row = await cur.fetchone()
    return row[0] if row and row[0] else None


async def case_anchor(conn: AsyncConnection, case_id: uuid.UUID) -> dt.date:
    """The date a two-digit year is read back from: the latest printed date in the bundle, else today."""
    return await latest_printed_date(conn, case_id) or dt.date.today()


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
