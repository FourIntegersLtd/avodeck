"""Whether a trial pack or offline copy is still current (state-machines.md "Trial pack and offline package
freshness"; architecture 10.7; S10, F35). Slice I writes both tables; slice G's timeline reads the newest
pack through `trial_steps`.

When slice I builds a pack or copy it stores what it was built from (`built_from`): the hearing's version,
the last numbered page, the newest served version (report, answers or supplementary) and the court bundle.
`stale_reason` compares that with the case now and gives the first reason it is out of date, as a short key
(no patient text): the court bundle was re-issued, the hearing was adjourned or changed, something newer
was served, or pages were added. Older rows without a snapshot are judged on their stored flag only.
Runs on the caller's connection, so row-level security applies.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from app.core.contracts.hearings import hearing_state

REASONS = ("court_bundle_superseded", "hearing_adjourned", "hearing_changed", "new_served_version", "new_pages")

# Snapshot fields and the columns slice I keeps them in (trial_packs, offline_packages).
COLUMNS = {
    "hearing_id": "hearing_id",
    "hearing_version": "built_hearing_version",
    "last_page_no": "built_last_page_no",
    "served_version_id": "built_served_version_id",
    "court_bundle_id": "court_bundle_id",
}


@dataclass(frozen=True)
class BuiltFrom:
    hearing_id: uuid.UUID | None = None
    hearing_version: int | None = None
    last_page_no: int | None = None
    served_version_id: uuid.UUID | None = None
    court_bundle_id: uuid.UUID | None = None


def _uuid(value: Any) -> uuid.UUID | None:
    return None if value is None else value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def built_from_row(row: Mapping[str, Any]) -> BuiltFrom:
    """The snapshot kept on a pack or copy row (a dict or `to_jsonb` of it); missing columns count as none."""
    version, last = row.get(COLUMNS["hearing_version"]), row.get(COLUMNS["last_page_no"])
    return BuiltFrom(
        hearing_id=_uuid(row.get("hearing_id")),
        hearing_version=None if version is None else int(version),
        last_page_no=None if last is None else int(last),
        served_version_id=_uuid(row.get(COLUMNS["served_version_id"])),
        court_bundle_id=_uuid(row.get(COLUMNS["court_bundle_id"])),
    )


async def _now(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[int | None, uuid.UUID | None]:
    cur = await conn.execute(
        "select (select max(bundle_page_no) from app.pages where case_id = %s),"
        " (select id from app.report_versions where case_id = %s and status = 'served'"
        " order by version_no desc limit 1)",
        (case_id, case_id),
    )
    row = await cur.fetchone()
    assert row is not None
    return (None if row[0] is None else int(row[0])), row[1]


async def built_from(
    conn: AsyncConnection, case_id: uuid.UUID, hearing_id: uuid.UUID | None, court_bundle_id: uuid.UUID | None = None
) -> BuiltFrom:
    """What a pack or copy built now for this hearing (or none) and court bundle (or none) is built from."""
    hearing = await hearing_state(conn, case_id, hearing_id) if hearing_id is not None else None
    last_page_no, served = await _now(conn, case_id)
    return BuiltFrom(hearing_id, hearing.version if hearing else None, last_page_no, served, court_bundle_id)


async def stale_reason(conn: AsyncConnection, case_id: uuid.UUID, built: BuiltFrom) -> str | None:
    """Why something built from `built` is out of date now, or `None` while it is current."""
    if built.court_bundle_id is not None:
        cur = await conn.execute(
            "select status from app.court_bundles where id = %s and case_id = %s", (built.court_bundle_id, case_id)
        )
        row = await cur.fetchone()
        if row is not None and row[0] == "superseded":
            return "court_bundle_superseded"
    if built.hearing_id is not None and built.hearing_version is not None:
        hearing = await hearing_state(conn, case_id, built.hearing_id)
        if hearing is not None and hearing.replaced_by_hearing_id is not None:
            return "hearing_adjourned"
        if hearing is None or hearing.version != built.hearing_version:
            return "hearing_changed"
    last_page_no, served = await _now(conn, case_id)
    if built.served_version_id is not None and served != built.served_version_id:
        return "new_served_version"
    if built.last_page_no is not None and (last_page_no or 0) > built.last_page_no:
        return "new_pages"
    return None
