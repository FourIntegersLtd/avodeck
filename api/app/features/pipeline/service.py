"""The bundle pipeline's jobs (architecture 7).

- `scan_document`: every document not yet scanned is scanned as it sits in Storage. An infected one
  is recorded and audited and never becomes pages; if a clean one is waiting, `process_bundle` is queued.
- `process_bundle` / `process_pages`: clean documents are cut into pages, numbered once after the
  case's last page in upload order, and each page's PDF, image and thumbnail are sealed and stored.

Every step checks what already exists, so a retried or repeated job changes nothing. Job rows and
logs carry numbers and step codes only (rule 6).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

from psycopg import AsyncConnection

from app.core import audit
from app.core.contracts.pipeline import PROCESS_BUNDLE, PROCESS_PAGES
from app.core.jobs import JobContext
from app.core.pdf import split_pages
from app.features.pipeline import paths, repo
from app.features.pipeline.render import render
from app.features.pipeline.wiring import PipelineServices, current

SCAN_DOCUMENT = "scan_document"
STEPS = ("split", "ocr", "duplicates", "extract", "sort", "gaps", "missing")


async def _noop(conn: AsyncConnection) -> None:
    return None


async def _chunks(
    conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, prefix: str
) -> AsyncIterator[bytes]:
    for path in sorted(await services.storage.list(prefix)):
        blob = await services.storage.get(path)
        yield await services.crypto.decrypt(conn, case_id, blob, purpose=path)


async def _read_document(conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, prefix: str) -> bytes:
    return b"".join([chunk async for chunk in _chunks(conn, services, case_id, prefix)])


async def _put_sealed(
    conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, path: str, data: bytes
) -> None:
    await services.storage.put(path, await services.crypto.encrypt(conn, case_id, data, purpose=path))


async def scan_document(ctx: JobContext) -> None:
    services = current()
    async with ctx.transaction() as conn:
        waiting = await repo.unscanned_documents(conn, ctx.case_id)
    for doc in waiting:
        async with ctx.transaction() as conn:
            result = await services.scanner.scan(_chunks(conn, services, ctx.case_id, doc.storage_path))
            added = await repo.record_scan(conn, ctx.case_id, doc.id, result.clean, result.signature)
            if added and not result.clean:
                await audit.record(conn, action="document.infected", target=f"document:{doc.id}", case_id=ctx.case_id)
    async with ctx.transaction() as conn:
        if await repo.clean_unsplit(conn, ctx.case_id):
            await services.registry.enqueue(conn, ctx.case_id, PROCESS_BUNDLE)
    await ctx.progress(100, step="scan")


async def _split(ctx: JobContext, services: PipelineServices) -> None:
    cache: dict[uuid.UUID, list[bytes]] = {}
    async with ctx.transaction() as conn:
        documents = await repo.clean_unsplit(conn, ctx.case_id)
    for doc in documents:
        async with ctx.transaction() as conn:
            pages = split_pages(await _read_document(conn, services, ctx.case_id, doc.storage_path))
            if await repo.add_pages(conn, ctx.case_id, doc.id, len(pages)):
                cache[doc.id] = pages
    async with ctx.transaction() as conn:
        waiting = await repo.pages_without_image(conn, ctx.case_id)
    for n, page in enumerate(waiting, start=1):
        async with ctx.transaction() as conn:
            if page.document_id not in cache:
                prefix = await repo.document_path(conn, page.document_id)
                cache[page.document_id] = split_pages(await _read_document(conn, services, ctx.case_id, prefix))
            page_pdf = cache[page.document_id][page.bundle_page_no - page.first_page_no]
            pictures = render(page_pdf)
            await _put_sealed(conn, services, ctx.case_id, paths.page_pdf_path(ctx.case_id, page.id), page_pdf)
            await _put_sealed(
                conn, services, ctx.case_id, paths.thumbnail_path(ctx.case_id, page.id), pictures.thumbnail
            )
            image_path = paths.page_image_path(ctx.case_id, page.id)
            await _put_sealed(conn, services, ctx.case_id, image_path, pictures.image)
            await repo.set_image(conn, page.id, image_path)
        if n % 25 == 0:
            await ctx.progress(min(30, 30 * n // len(waiting)), step="split")


async def process_bundle(ctx: JobContext) -> None:
    services = current()
    await ctx.progress(0, step="split")
    await _split(ctx, services)
    for n, step in enumerate(STEPS, start=1):
        await ctx.step(step, _noop)
        await ctx.progress(100 * n // len(STEPS), step=step)


async def queue_pages(conn: AsyncConnection, case_id: uuid.UUID, page_ids: object) -> uuid.UUID:
    """The `process_pages` entry point other slices call (core/contracts/pipeline.py)."""
    from app.features.pipeline.wiring import registry

    return await registry().enqueue(conn, case_id, PROCESS_PAGES)
