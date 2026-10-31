"""Slice B job: `export_transcript` builds the Word copy of the records transcript (ADR 0028).

The file holds the entries as the screen does, in the same order, with names and dates of birth
stripped (rule 7). It is stored sealed under the case key and handed over once, to the person who asked
(the route in `transcript_routes`); slice H's daily run removes a copy nobody took (`EXPORT_KEEP_DAYS`).
Logs carry exception types only and never a word of the record (rule 6).
"""

from __future__ import annotations

import io
import uuid

import docx
from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ApiError
from app.core.jobs import JobContext, JobRegistry, registry
from app.core.packs import PackLibrary, default_library
from app.core.storage import StorageError, SupabaseStorage, case_prefix, storage_settings
from app.features.reader import transcript

KIND = "export_transcript"
PAGE = 200


class _Services:
    """Storage, the case keys and the packs for the job, made from the cell config on first use."""

    def __init__(self) -> None:
        self.storage: SupabaseStorage | None = None
        self.crypto: CaseCrypto | None = None
        self.packs: PackLibrary | None = None

    def get_storage(self) -> SupabaseStorage:
        if self.storage is None:
            self.storage = SupabaseStorage(storage_settings())
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


def transcript_path(case_id: uuid.UUID, job_id: uuid.UUID) -> str:
    return f"{case_prefix(case_id)}/exports/{job_id}/transcript.docx"


def _label(key: str) -> str:
    return key.replace("_", " ").capitalize()


def build(entries: list[transcript.TranscriptEntry]) -> bytes:
    document = docx.Document()
    document.add_heading("Records transcript", level=0)
    for e in entries:
        parts = [f"Page {e.page_no}", e.event_date.isoformat() if e.event_date else "undated", _label(e.kind)]
        if e.author_role:
            parts.append(_label(e.author_role))
        document.add_heading(", ".join(parts), level=2)
        for line in e.lines:
            document.add_paragraph(line)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


async def export_transcript(ctx: JobContext) -> None:
    """Builds the requester's copy. A job nobody asked for (`requested_by` empty) builds nothing."""
    storage, crypto, library = services.get_storage(), services.get_crypto(), services.get_packs()
    async with ctx.transaction() as conn:
        cur = await conn.execute(
            "select 1 from app.jobs where id = %s and requested_by is not null and exists"
            " (select 1 from app.cases where id = %s and status <> 'deleted')",
            (ctx.job_id, ctx.case_id),
        )
        if await cur.fetchone() is None:
            return
        everything: list[transcript.TranscriptEntry] = []
        while True:
            got = await transcript.entries(conn, crypto, library, ctx.case_id, offset=len(everything), limit=PAGE)
            everything += got.entries
            if not got.entries or len(everything) >= got.total:
                break
        path = transcript_path(ctx.case_id, ctx.job_id)
        await storage.put(path, await crypto.encrypt(conn, ctx.case_id, build(everything), purpose=path))


async def take(
    conn: AsyncConnection, storage: SupabaseStorage, crypto: CaseCrypto, case_id: uuid.UUID, job_id: uuid.UUID
) -> bytes:
    """The Word file, once: the stored copy is removed as it is handed over."""
    path = transcript_path(case_id, job_id)
    await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (path,))
    try:
        blob = await storage.get(path)
    except StorageError:
        raise ApiError("not_found", "This file has already been downloaded.", 404) from None
    data = await crypto.decrypt(conn, case_id, blob, purpose=path)
    await storage.delete([path])
    return data


def register(reg: JobRegistry) -> None:
    reg.kind(KIND, changes_case=False, runs_on_hold=True)(export_transcript)


register(registry)
