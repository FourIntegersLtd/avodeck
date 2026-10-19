"""SQL for slice I's tables, and the reads of other slices' data slice I needs (through contracts)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.rows import dict_row

from app.core.contracts.facts import CITABLE_STATUSES
from app.core.contracts.served import ServedReport
from app.core.crypto import CaseCrypto
from app.features.trial.common import (
    AGENDA_TEXT,
    AGREED_REASON,
    CHANGE_NOTE,
    NEW_NOTE,
    OTHER_POSITIONS,
    POSITION,
    READINESS_REPLY,
    unseal,
)


async def one(conn: AsyncConnection, sql: str, args: tuple[Any, ...]) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, args)
        return await cur.fetchone()


async def many(conn: AsyncConnection, sql: str, args: tuple[Any, ...]) -> list[dict[str, Any]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql, args)
        return await cur.fetchall()


# --- meetings ----------------------------------------------------------------------------------------------


async def meeting(conn: AsyncConnection, case_id: uuid.UUID, meeting_id: uuid.UUID) -> dict[str, Any] | None:
    return await one(conn, "select * from app.expert_meetings where case_id = %s and id = %s", (case_id, meeting_id))


async def agenda_versions(conn: AsyncConnection, meeting_id: uuid.UUID) -> list[dict[str, Any]]:
    return await many(
        conn,
        "select id, version_no, read_at, item_count, created_at from app.agenda_versions"
        " where meeting_id = %s order by version_no",
        (meeting_id,),
    )


async def items(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, version_id: uuid.UUID
) -> list[dict[str, Any]]:
    """The agenda items of one agenda version, opened."""
    rows = await many(
        conn, "select * from app.agenda_items where agenda_version_id = %s order by number", (version_id,)
    )
    for row in rows:
        await open_item(conn, crypto, case_id, row)
    return rows


async def open_item(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, row: dict[str, Any]) -> None:
    row["text"] = await unseal(conn, crypto, case_id, row["text"], AGENDA_TEXT)
    row["own_position"] = await unseal(conn, crypto, case_id, row["own_position"], POSITION)
    row["other_positions"] = await unseal(conn, crypto, case_id, row["other_positions"], OTHER_POSITIONS)
    row["reason"] = await unseal(conn, crypto, case_id, row["reason"], AGREED_REASON)
    row["change_note"] = await unseal(conn, crypto, case_id, row["change_note"], CHANGE_NOTE)


async def current_items(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, meeting_id: uuid.UUID
) -> list[dict[str, Any]]:
    versions = await agenda_versions(conn, meeting_id)
    read = [v for v in versions if v["read_at"] is not None]
    return await items(conn, crypto, case_id, read[-1]["id"]) if read else []


async def latest_meeting(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, Any] | None:
    """The case's most recent meeting, signed ones first."""
    return await one(
        conn,
        "select * from app.expert_meetings where case_id = %s"
        " order by (status in ('signed', 'signed_external')) desc, created_at desc limit 1",
        (case_id,),
    )


# --- court bundles and the concordance ---------------------------------------------------------------------


async def current_bundle(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, Any] | None:
    return await one(
        conn,
        "select * from app.court_bundles where case_id = %s and status <> 'superseded'"
        " order by created_at desc limit 1",
        (case_id,),
    )


def status_of(row: dict[str, Any]) -> str:
    if row["trial_page_no"] is None:
        return "missing"
    return "matched" if row["confirmed"] else "to_confirm"


async def concordance(conn: AsyncConnection, bundle_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await many(
        conn,
        "select k.*, p.source_type, (select min(f.event_date) from app.facts f where f.page_id = k.page_id"
        " and f.status = any(%s)) as document_date from app.concordance k join app.pages p on p.id = k.page_id"
        " where k.court_bundle_id = %s order by k.page_no",
        (sorted(CITABLE_STATUSES), bundle_id),
    )
    for row in rows:
        row["status"] = status_of(row)
    return rows


def paragraphs_by_page(served: ServedReport | None) -> dict[uuid.UUID, list[int]]:
    """The served paragraphs (numbers) citing each page."""
    out: dict[uuid.UUID, list[int]] = {}
    for paragraph in served.paragraphs if served else ():
        for page_id, _ in paragraph.cited:
            numbers = out.setdefault(page_id, [])
            if paragraph.para_no not in numbers:
                numbers.append(paragraph.para_no)
    return out


@dataclass(frozen=True)
class PageRef:
    page_no: int
    trial_page_no: int | None
    status: str


async def page_refs(conn: AsyncConnection, case_id: uuid.UUID) -> dict[int, PageRef]:
    """Each page's trial number in the current court bundle, by its bundle page number."""
    bundle = await current_bundle(conn, case_id)
    if bundle is None or bundle["status"] != "matched":
        return {}
    return {
        r["page_no"]: PageRef(r["page_no"], r["trial_page_no"], r["status"])
        for r in await concordance(conn, bundle["id"])
    }


async def page_numbers(conn: AsyncConnection, page_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    cur = await conn.execute("select id, bundle_page_no from app.pages where id = any(%s)", (page_ids,))
    return {pid: int(no) for pid, no in await cur.fetchall() if no is not None}


async def page_sources(conn: AsyncConnection, page_ids: list[uuid.UUID]) -> dict[uuid.UUID, str]:
    """The record source type slice A gave each page (a key of the pack's `source_types`), where it has one."""
    cur = await conn.execute("select id, source_type from app.pages where id = any(%s)", (page_ids,))
    return {pid: str(kind) for pid, kind in await cur.fetchall() if kind}


# --- what the expert prepares for the day ---------------------------------------------------------------------


async def replies(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> dict[int, tuple[str, datetime]]:
    """The expert's reply to each readiness question, by question number, with when it was written."""
    rows = await many(
        conn, "select question_no, reply, replied_at from app.readiness_replies where case_id = %s", (case_id,)
    )
    return {
        r["question_no"]: (str(await unseal(conn, crypto, case_id, r["reply"], READINESS_REPLY)), r["replied_at"])
        for r in rows
    }


async def notes(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> list[dict[str, Any]]:
    """The note sentences kept for the case, oldest first, opened."""
    rows = await many(conn, "select * from app.trial_notes where case_id = %s order by created_at, id", (case_id,))
    for row in rows:
        row["text"] = await unseal(conn, crypto, case_id, row["text"], NEW_NOTE)
    return rows
