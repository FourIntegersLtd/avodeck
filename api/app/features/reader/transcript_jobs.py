"""Slice B job: `export_transcript` builds the Word and PDF copies of the records transcript (ADR 0028).

The files hold every entry, in the pack's sections and date order, with names and dates of birth
stripped (rule 7). It is stored sealed under the case key and handed over once, to the person who asked
(the route in `transcript_routes`); slice H's daily run removes a copy nobody took (`EXPORT_KEEP_DAYS`).
Logs carry exception types only and never a word of the record (rule 6).
"""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from psycopg import AsyncConnection

from app.core import audit
from app.core.contracts.availability import evidence_for_case
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ApiError
from app.core.jobs import JobContext, JobRegistry, registry
from app.core.packs import MergedPack, PackLibrary, default_library
from app.core.storage import CaseFileStorage, StorageError, case_prefix, storage_settings
from app.features.reader import repo, transcript, transcript_docx, transcript_layout, transcript_pdf

log = logging.getLogger(__name__)

KIND = "export_transcript"
KIND_PDF = "export_transcript_pdf"
PAGE = 200
DOWNLOADED = "case.transcript_downloaded"


class _Services:
    """Storage, the case keys and the packs for the job, made from the cell config on first use."""

    def __init__(self) -> None:
        self.storage: CaseFileStorage | None = None
        self.crypto: CaseCrypto | None = None
        self.packs: PackLibrary | None = None

    def get_storage(self) -> CaseFileStorage:
        if self.storage is None:
            self.storage = CaseFileStorage(storage_settings())
        return self.storage

    def get_crypto(self) -> CaseCrypto:
        if self.crypto is None:
            self.crypto = CaseCrypto(key_provider_from_env())
        return self.crypto

    def get_packs(self) -> PackLibrary:
        if self.packs is None:
            self.packs = default_library()
        return self.packs


services = _Services()


def transcript_path(case_id: uuid.UUID, job_id: uuid.UUID, fmt: str = "docx") -> str:
    return f"{case_prefix(case_id)}/exports/{job_id}/transcript.{fmt}"


async def _layout(
    ctx: JobContext, conn: AsyncConnection
) -> tuple[transcript_layout.Layout, Any, dict[int, date], transcript_layout.Coverage] | None:
    """The file's layout for the requester's job, or None for a job nobody asked for or a case gone."""
    cur = await conn.execute(
        "select 1 from app.jobs where id = %s and requested_by is not null and exists"
        " (select 1 from app.cases where id = %s and status <> 'deleted')",
        (ctx.job_id, ctx.case_id),
    )
    if await cur.fetchone() is None:
        return None
    cur = await conn.execute("select named_expert_id from app.cases where id = %s", (ctx.case_id,))
    named = (await cur.fetchone() or (None,))[0]
    codes = await repo.case_codes(conn, ctx.case_id)
    if named is None or codes is None:
        return None
    crypto, library = services.get_crypto(), services.get_packs()
    pack = library.for_case(codes)
    everything: list[transcript.TranscriptEntry] = []
    pictures: list[int] = []
    unread: list[int] = []
    while True:
        got = await transcript.entries(
            conn, crypto, library, ctx.case_id, offset=len(everything), limit=PAGE, full=True
        )
        everything += got.entries
        pictures, unread = got.picture_pages, got.unread_pages
        if not got.entries or len(everything) >= got.total:
            break
    total, set_aside = await repo.page_counts(conn, ctx.case_id)
    coverage = transcript_layout.Coverage(
        total_pages=total,
        with_entries=len({e.page_no for e in everything}),
        no_entries=len(pictures),
        unreadable=len(unread),
        set_aside=set_aside,
    )
    layout = transcript_layout.arrange(
        everything, pack, no_entry_pages=pictures, unread_pages=unread, coverage=coverage
    )
    letter = pack.letter_entry_kind()
    received: dict[int, date] = {}
    if letter is not None and any(e.kind == letter for e in everything):
        for ev in await evidence_for_case(conn, ctx.case_id):
            if ev.kind in ("received", "filed") and ev.date is not None:
                received[ev.page_no] = min(ev.date, received.get(ev.page_no, ev.date))
        letters = {e.page_no for e in everything if e.kind == letter}
        received = {n: d for n, d in received.items() if n in letters}
    return layout, pack, received, coverage


def _today(pack: MergedPack) -> date:
    """The date the file is prepared, in the pack's timezone."""
    return datetime.now(ZoneInfo(pack.timezone)).date()


async def export_transcript(ctx: JobContext) -> None:
    """Builds the requester's Word copy. A job nobody asked for (`requested_by` empty) builds nothing."""
    storage, crypto = services.get_storage(), services.get_crypto()
    async with ctx.transaction() as conn:
        made = await _layout(ctx, conn)
        if made is None:
            return
        layout, pack, received, _ = made
        document = transcript_docx.build(
            layout, date_order=pack.date_order, language=pack.language, received=received, prepared_on=_today(pack)
        )
        path = transcript_path(ctx.case_id, ctx.job_id)
        await storage.put(path, await crypto.encrypt(conn, ctx.case_id, document, purpose=path))


async def _picture(
    conn: AsyncConnection, storage: CaseFileStorage, crypto: CaseCrypto, case_id: uuid.UUID, path: str
) -> bytes | None:
    """One page picture, opened in memory only; None when it cannot be fetched (the file goes on without it)."""
    try:
        # shrunk at once, so a long bundle never holds every full-size picture at the same time
        return transcript_pdf.prepare_picture(
            await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path)
        )
    except Exception as error:  # noqa: BLE001 - one missing picture must not stop the file; only the type is logged
        log.warning("transcript picture unavailable (%s)", type(error).__name__)
        return None


async def export_transcript_pdf(ctx: JobContext) -> None:
    """Builds the requester's PDF: the same layout as the Word copy, then a picture of every page."""
    storage, crypto = services.get_storage(), services.get_crypto()
    async with ctx.transaction() as conn:
        made = await _layout(ctx, conn)
        if made is None:
            return
        layout, pack, received, _ = made
        pictures: list[tuple[int, bytes | None]] = []
        for number, image_path in await repo.page_pictures(conn, ctx.case_id):
            pictures.append(
                (number, await _picture(conn, storage, crypto, ctx.case_id, image_path) if image_path else None)
            )
        document = transcript_pdf.build_pdf(
            layout, pictures, date_order=pack.date_order, language=pack.language, received=received,
            prepared_on=_today(pack),
        )  # fmt: skip
        path = transcript_path(ctx.case_id, ctx.job_id, "pdf")
        await storage.put(path, await crypto.encrypt(conn, ctx.case_id, document, purpose=path))


def _missing(error: StorageError) -> bool:
    """True when storage said the object is not there (not any other fault)."""
    return str(error).rsplit(" ", 1)[-1] in ("400", "404")


async def take(
    conn: AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    case_id: uuid.UUID,
    job_id: uuid.UUID,
    fmt: str = "docx",
) -> bytes:
    """The Word file, once. That it was taken is the audit row, so a copy that could not be removed is never
    handed over twice; the caller removes the stored copy after the transaction commits (`remove`)."""
    path = transcript_path(case_id, job_id, fmt)
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (path,))
    cur = await conn.execute(
        "select 1 from app.audit_log where case_id = %s and action = %s and target = %s",
        (case_id, DOWNLOADED, f"export:{job_id}"),
    )
    if await cur.fetchone() is not None:
        raise ApiError("not_found", "This file has already been downloaded.", 404)
    try:
        blob = await storage.get(path)
    except StorageError as error:
        if _missing(error):
            raise ApiError("not_found", "This file has already been downloaded.", 404) from None
        raise ApiError("unavailable", "File storage is not ready.", 503) from None
    data = await crypto.decrypt(conn, case_id, blob, purpose=path)
    await audit.record(conn, action=DOWNLOADED, target=f"export:{job_id}", case_id=case_id)
    return data


async def remove(storage: CaseFileStorage, case_id: uuid.UUID, job_id: uuid.UUID, fmt: str = "docx") -> None:
    """Removes the stored copy once the download is recorded. A failure leaves it for the daily sweep."""
    try:
        await storage.delete([transcript_path(case_id, job_id, fmt)])
    except StorageError:
        pass


def register(reg: JobRegistry) -> None:
    reg.kind(KIND, changes_case=False, runs_on_hold=True)(export_transcript)
    reg.kind(KIND_PDF, changes_case=False, runs_on_hold=True)(export_transcript_pdf)


register(registry)
