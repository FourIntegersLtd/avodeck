"""The curated guideline table: which version was in force on a date (labels, dates and links only,
never guideline text). Rows come from the overlay pack (`pack.guidelines()`). The version each case was
shown is kept in `app.case_guideline_versions` with a fingerprint of the entry, so a later change to the
pack's entry shows a notice until the checks are re-run."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

from psycopg import AsyncConnection


def _day(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10]) if value else None


def in_force(rows: Iterable[Mapping[str, Any]], code: str, on: date) -> Mapping[str, Any] | None:
    for row in rows:
        start, end = _day(row.get("in_force_from")), _day(row.get("in_force_to"))
        if row.get("code") == code and start is not None and start <= on and (end is None or on < end):
            return row
    return None


# The versions each case was shown (app.case_guideline_versions) -----------------------------------------------

_ENTRY_FIELDS = ("code", "version_label", "title", "in_force_from", "in_force_to", "url")


def entry_hash(row: Mapping[str, Any]) -> str:
    """A fingerprint of a pack's guideline entry (labels, dates and link only), to notice a later change."""
    joined = "\x1f".join(str(row.get(k) or "") for k in _ENTRY_FIELDS)
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


def _reviewed(row: Mapping[str, Any]) -> date | None:
    return _day(row.get("reviewed_at"))


async def record_shown(conn: AsyncConnection, case_id: uuid.UUID, rows: Iterable[Mapping[str, Any]]) -> None:
    """Remember the version of each entry the case was first shown (kept until a re-run)."""
    for row in rows:
        await conn.execute(
            "insert into app.case_guideline_versions (case_id, code, version_label, table_reviewed_at, entry_hash)"
            " values (%s, %s, %s, %s, %s) on conflict do nothing",
            (case_id, str(row["code"]), str(row["version_label"]), _reviewed(row), entry_hash(row)),
        )


async def _recorded(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[str, str, str | None]]:
    cur = await conn.execute(
        "select code, version_label, entry_hash from app.case_guideline_versions where case_id = %s", (case_id,)
    )
    return [(str(r[0]), str(r[1]), r[2]) for r in await cur.fetchall()]


def _entry(rows: Iterable[Mapping[str, Any]], code: str, version_label: str) -> Mapping[str, Any] | None:
    return next((r for r in rows if r.get("code") == code and str(r.get("version_label")) == version_label), None)


async def changed(conn: AsyncConnection, case_id: uuid.UUID, rows: Sequence[Mapping[str, Any]]) -> list[str]:
    """Codes whose entry in the pack differs from (or no longer lists) the version the case was shown."""
    out = set()
    for code, version_label, shown in await _recorded(conn, case_id):
        now = _entry(rows, code, version_label)
        if now is None or entry_hash(now) != shown:
            out.add(code)
    return sorted(out)


async def rebaseline(conn: AsyncConnection, case_id: uuid.UUID, rows: Sequence[Mapping[str, Any]]) -> None:
    """On a re-run the case takes the pack's current entries as shown; entries the pack dropped are forgotten."""
    for code, version_label, _ in await _recorded(conn, case_id):
        now = _entry(rows, code, version_label)
        if now is None:
            await conn.execute(
                "delete from app.case_guideline_versions where case_id = %s and code = %s and version_label = %s",
                (case_id, code, version_label),
            )
        else:
            await conn.execute(
                "update app.case_guideline_versions set entry_hash = %s, table_reviewed_at = %s"
                " where case_id = %s and code = %s and version_label = %s",
                (entry_hash(now), _reviewed(now), case_id, code, version_label),
            )
