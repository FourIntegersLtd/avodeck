"""The bundle pipeline's jobs (architecture 7).

- `scan_document`: every document not yet scanned is scanned as it sits in Storage. An infected one
  is recorded and audited and never becomes pages; if a clean one is waiting, `process_bundle` is queued.
- `process_bundle` / `process_pages`: clean documents are cut into pages, numbered once after the
  case's last page in upload order, and each page's PDF, image and thumbnail are sealed and stored.
  Each page's words come from its text layer or OCR (T-07a); then each page is sent once to the
  model (T-07b) with identifiers stripped. Facts not found in their cited words are refused and the
  page is asked once more, then marked `needs_human`; facts citing crossed-out words are dropped.
  Each fact's date is the printed date the model points at, read by code in the pack's date order;
  one that is not printed in full is marked `please_check` and never guessed. Then each new page is
  compared with the case's pages (picture and word hashes, then word by word) and a copy is set
  aside pointing at the earliest page it copies; it keeps its number, pictures and facts (F13).
- `process_pages` with named pages (`queue_pages`) reads them again: new pictures in the page's
  `rotation`, words read again and turned with it. `extract_page_text` (`queue_page_text`) takes the
  page's words from the expert's typed-out text instead. A page read again keeps its number; its
  unchecked suggestions are superseded and it is extracted again; each checked fact moves onto the
  same words in the new reading, or is flagged "Please check" when they are gone.

Every step checks what already exists, so a retried or repeated job changes nothing. Job rows and
logs carry numbers and step codes only (rule 6).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Any

import pikepdf
from psycopg import AsyncConnection

from app.core import audit, notifications
from app.core.ai import embed, pii, provenance
from app.core.ai.answers import page_subject, reference_subject
from app.core.ai.bedrock import ServiceBusy
from app.core.ai.cell import model_for
from app.core.ai.kit import InvalidOutput, OutputTruncated, TooLargeForFallback
from app.core.ai.ocr import OcrFailed, OcrPage
from app.core.contracts.fact_dates import case_anchor, latest_printed_date
from app.core.contracts.fact_flags import flag_for_recheck
from app.core.contracts.fact_verifications import log_remaps
from app.core.contracts.pipeline import PROCESS_BUNDLE, PROCESS_PAGES, transcription_purpose
from app.core.contracts.record_entries import RETRY_UNREAD
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext
from app.core.pdf import split_pages
from app.core.scan import ScanUnavailable
from app.features.pipeline import (
    convert,
    coverage,
    date_repair,
    dates,
    duplicates,
    extract,
    gaps,
    language,
    missing,
    paths,
    quarantine,
    repo,
    steplog,
)
from app.features.pipeline.asking import answer_group
from app.features.pipeline.render import Pictures, encode, rasterise
from app.features.pipeline.straighten import quarter_turn
from app.features.pipeline.wiring import PipelineServices, current
from app.features.pipeline.words import assess, turned, typed, usable_text_layer

SCAN_DOCUMENT = "scan_document"
REPROCESS_CASE = "reprocess_case"  # the expert's opt-in to extract every page again
RETRY_UNREAD_PAGES = RETRY_UNREAD  # extract again only the pages the model could not read
EXTRACT_ENTRIES = "extract_entries"  # read entries only, for a case extracted before entries existed (ADR 0028)
REPAIR_FACT_DATES = "repair_fact_dates"  # fill event_date from printed words when extraction left it null
PURGE_QUARANTINED = "purge_quarantined_pages"  # the daily deletion of quarantined pages' images and text
# How many pages run at once, by what the work waits on. No setting: a limit is a fact about the vendor
# or the machine, not a choice for whoever runs the cell.
CONCURRENCY = 3  # model calls (extraction, references, entries); the AI kit's own limits replace it
OCR_CONCURRENCY = 30  # Azure S0 allows 15 submits/s; 30 pages in flight at ~5 s each is ~6 submits/s
STORAGE_CONCURRENCY = 20  # S3-only work (reading pictures back, writing page files): latency, not bandwidth
DB_CONCURRENCY = 6  # database-only work: each task holds a pooled connection, and the pool has to keep spare ones
POOL_RESERVE = 2  # worker connections left free (heartbeat, progress, the job's own); model calls stay under the rest
CPU_CONCURRENCY = min(  # the cores this process may use (not the host's), at most 8
    8, len(os.sched_getaffinity(0)) if hasattr(os, "sched_getaffinity") else (os.cpu_count() or 2)
)
_CPU = ThreadPoolExecutor(max_workers=CPU_CONCURRENCY, thread_name_prefix="pipeline-cpu")  # JPEG and hash work
# PDFium is not thread-safe, so every call into it goes through this one thread: the event loop stays free
# (the worker's heartbeat, progress and other pages keep moving) and two calls never overlap.
_PDFIUM = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pdfium")
LIMITS = convert.Limits()
PART_OBJECT = "file"  # the one sealed object under a part's prefix
log = logging.getLogger(__name__)

STEPS = ("split", "ocr", "sort", "duplicates", "missing")  # the steps the Preparing screen shows
READY = "bundle_ready"  # the notification kind sent when new pages are ready


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
        try:
            # One chunk at a time, each read from S3 with no connection and opened in its own short transaction;
            # the scanner itself is called with no connection held.
            result = await services.scanner.scan(_chunks_free(ctx, services, doc.storage_path))
        except ScanUnavailable:
            # Fail closed (F42): nothing is recorded, so the file is not accepted; the queue retries.
            log.warning("virus scanner unreachable; the document stays unscanned")
            raise
        async with ctx.transaction() as conn:
            added = await repo.record_scan(conn, ctx.case_id, doc.id, result.clean, result.signature)
            if added and not result.clean:
                await audit.record(conn, action="document.infected", target=f"document:{doc.id}", case_id=ctx.case_id)
    async with ctx.transaction() as conn:
        if await repo.clean_unsplit(conn, ctx.case_id):
            await services.registry.enqueue(conn, ctx.case_id, PROCESS_BUNDLE)
    await ctx.progress(100, step="scan")


def _pages_of(found: convert.Unpacked) -> list[bytes]:
    return split_pages(found.pdf) if found.pdf is not None else []


async def _unpack(
    conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, doc: repo.Document, data: bytes
) -> list[bytes]:
    """Cuts one document into pages; the files inside an email or a zip become documents of their
    own (cut on the next round) and files that cannot be paged become exhibits (F36)."""
    found = await asyncio.to_thread(convert.unpack, data, doc.filename, LIMITS)
    if found.members and await repo.part_depth(conn, doc.id) >= LIMITS.max_depth:
        found = convert.Unpacked(found.media_type, exhibit=True)
    for n, member in enumerate(found.members, start=1):
        child = uuid.uuid4()
        prefix = paths.part_prefix(case_id, child)
        made = await repo.add_part(
            conn,
            case_id,
            doc.id,
            n,
            document_id=child,
            filename=member.filename,
            media_type=convert.sniff(member.data, member.filename),
            sha256=hashlib.sha256(member.data).hexdigest(),
            storage_path=prefix,
        )
        if made:
            await _put_sealed(conn, services, case_id, f"{prefix}/{PART_OBJECT}", member.data)
    if found.exhibit:
        await repo.add_exhibit(
            conn,
            case_id,
            doc.id,
            media_type=found.media_type,
            size=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            storage_path=doc.storage_path,
        )
    await repo.set_media_type(conn, doc.id, found.media_type)
    return await asyncio.to_thread(_pages_of, found)


async def _split(ctx: JobContext, services: PipelineServices) -> None:
    cache: dict[uuid.UUID, list[bytes]] = {}
    while True:
        async with ctx.transaction() as conn:
            documents = await repo.clean_unsplit(conn, ctx.case_id)
        if not documents:
            break
        for doc in documents:
            data = await _read_document_free(ctx, services, doc.storage_path)  # S3 with no connection held
            async with ctx.transaction() as conn:
                try:
                    pages = await _unpack(conn, services, ctx.case_id, doc, data)
                except (pikepdf.PasswordError, pikepdf.PdfError) as error:  # F2: this file stops, the rest go on
                    log.warning("document could not be opened: %s", type(error).__name__)  # class only, no text
                    reason = "locked" if isinstance(error, pikepdf.PasswordError) else "damaged"
                    await repo.mark_unopened(conn, ctx.case_id, doc.id, reason)
                    continue
                if await repo.add_pages(conn, ctx.case_id, doc.id, len(pages)):
                    cache[doc.id] = pages
                await repo.link_replacements(conn, ctx.case_id)
                await repo.link_splits(conn, ctx.case_id)
    async with ctx.transaction() as conn:
        waiting = await repo.pages_without_image(conn, ctx.case_id)
    locks: dict[uuid.UUID, asyncio.Lock] = {}
    finished = 0

    async def pages_of(page: repo.PageRow) -> list[bytes]:
        """The document's one-page PDFs: from this run's cache, else read back from S3 (once per document)."""
        async with locks.setdefault(page.document_id, asyncio.Lock()):
            if page.document_id not in cache:
                async with ctx.transaction() as conn:
                    prefix, filename = await repo.document_file(conn, page.document_id)
                data = await _read_document_free(ctx, services, prefix)
                unpacked = await asyncio.to_thread(convert.unpack, data, filename, LIMITS)
                cache[page.document_id] = await asyncio.to_thread(_pages_of, unpacked)
            return cache[page.document_id]

    async def make(page: repo.PageRow) -> None:
        nonlocal finished
        page_pdf = (await pages_of(page))[page.bundle_page_no - page.first_page_no]
        pictures = await _pictures(page_pdf, page.rotation)
        image_path = paths.page_image_path(ctx.case_id, page.id)
        thumbnail_path = paths.thumbnail_path(ctx.case_id, page.id)
        page_path = paths.page_pdf_path(ctx.case_id, page.id)
        sealed = await _seal_all(
            ctx,
            services,
            [(page_path, page_pdf), (thumbnail_path, pictures.thumbnail), (image_path, pictures.image)],
        )
        for path, blob in sealed:  # S3 with no connection held
            await services.storage.put(path, blob)
        async with ctx.transaction() as conn:
            await repo.set_image(conn, page.id, image_path)
        finished += 1
        if finished % 25 == 0:
            await ctx.progress(min(30, 30 * finished // len(waiting)), step="split")

    await bounded(waiting, make, STORAGE_CONCURRENCY, _stop(ctx), label=lambda page: page.bundle_page_no)


async def _pictures(page_pdf: bytes, rotation: int) -> Pictures:
    """The page picture and thumbnail. PDFium is not thread-safe, so the page is drawn on its one thread
    (never the event loop's); the JPEG encoding, which is most of the work, runs in a CPU thread."""
    loop = asyncio.get_running_loop()
    image = await loop.run_in_executor(_PDFIUM, rasterise, page_pdf, rotation)
    return await loop.run_in_executor(_CPU, encode, image)


async def _seal_all(
    ctx: JobContext, services: PipelineServices, files: Sequence[tuple[str, bytes]]
) -> list[tuple[str, bytes]]:
    """Encrypts each file in one short transaction (the key is cached; no storage call is made inside)."""
    async with ctx.transaction() as conn:
        return [(path, await services.crypto.encrypt(conn, ctx.case_id, data, purpose=path)) for path, data in files]


async def _chunks_free(ctx: JobContext, services: PipelineServices, prefix: str) -> AsyncIterator[bytes]:
    """A stored document's chunks, one at a time: read from S3 with no connection held, then opened in a
    short transaction of its own. At most one sealed and one opened chunk are in memory at once."""
    for path in sorted(await services.storage.list(prefix)):
        blob = await services.storage.get(path)
        async with ctx.transaction() as conn:
            opened = await services.crypto.decrypt(conn, ctx.case_id, blob, purpose=path)
        del blob
        yield opened


async def _read_document_free(ctx: JobContext, services: PipelineServices, prefix: str) -> bytes:
    return b"".join([chunk async for chunk in _chunks_free(ctx, services, prefix)])


Stop = Callable[[], Awaitable[bool]]


async def bounded[T](
    items: Sequence[T],
    work: Callable[[T], Awaitable[None]],
    limit: int = CONCURRENCY,
    stop: Stop | None = None,
    *,
    label: Callable[[T], int | None] | None = None,
) -> None:
    """Run `work` on every item, at most `limit` at once; the first error stops the rest.

    With `stop`, it is asked before each item starts: once it says yes, no new item starts, items
    already running finish, and the job pauses with reason `cancelled`.

    With `label` (the item's page number), the step being logged (`steplog`) is told how long each page
    took and which page failed with what; the error itself still stops the rest.
    """
    gate = asyncio.Semaphore(limit)
    stopped = False
    run = steplog.current() if label is not None else None

    async def one(item: T) -> None:
        nonlocal stopped
        async with gate:
            if stopped or (stop is not None and await stop()):
                stopped = True
                return
            if run is None or label is None:
                await work(item)
                return
            started = time.perf_counter()
            try:
                await work(item)
            except Exception as exc:
                page = label(item)
                if not run.has_failed(page):
                    outcome = "paused" if isinstance(exc, ProcessingPaused) else "failed"
                    run.page_failed(page, exc, outcome, (time.perf_counter() - started) * 1000)
                raise
            run.page_ok(label(item), (time.perf_counter() - started) * 1000)

    tasks = [asyncio.create_task(one(item)) for item in items]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    if stopped:
        raise ProcessingPaused("cancelled")


def _stop(ctx: JobContext) -> Stop:
    """Whether the expert asked to stop processing after this job was queued."""

    async def asked() -> bool:
        async with ctx.transaction() as conn:
            return await repo.cancel_requested(conn, ctx.job_id)

    return asked


async def _page_pdf(conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, page_id: uuid.UUID) -> bytes:
    path = paths.page_pdf_path(case_id, page_id)
    return await services.crypto.decrypt(conn, case_id, await services.storage.get(path), purpose=path)


async def _opened_words(
    conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, page_id: uuid.UUID
) -> list[tuple[str, str]]:
    return [
        (
            w.word_id,
            (
                await services.crypto.decrypt(conn, case_id, w.sealed, purpose=paths.word_purpose(page_id, w.word_id))
            ).decode(),
        )
        for w in await repo.words_of(conn, page_id)
    ]


def _same_words(wanted: Sequence[str], value: str | None, words: Sequence[tuple[str, str]]) -> list[str] | None:
    """The ids of the first unbroken run of `words` that reads exactly as `wanted` once both are cut
    into rule 2's atoms (`provenance.atoms`: "1.5" is not "15", "<60" is not "60"), and in which the
    fact's value still passes the provenance check."""
    target = provenance.atoms(" ".join(wanted))
    if not target:
        return None
    texts = dict(words)
    for start in range(len(words)):
        for end in range(start, len(words)):
            found = provenance.atoms(" ".join(text for _, text in words[start : end + 1]))
            if len(found) >= len(target):
                break
        if found == target:
            ids = [word_id for word_id, _ in words[start : end + 1]]
            if value is None or provenance.supported(value, ids, texts):
                return ids
    return None


async def _settle(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    before: list[tuple[str, str]],
    page: OcrPage,
    placed: bool = True,
) -> None:
    """A page read again: each checked fact moves onto the same words (logged as `remapped`), or is
    flagged "Please check". Words with no place on the picture (`placed=False`, typed out) give no box."""
    old = dict(before)
    new = [(w.id, w.text) for w in page.words]
    polygons = {w.id: list(w.polygon) for w in page.words}
    moved: dict[uuid.UUID, tuple[list[str], dict[str, float] | None]] = {}
    logged: list[tuple[uuid.UUID, str | None, list[str], list[str]]] = []
    lost: list[uuid.UUID] = []
    for fact_id, word_ids, value in await repo.checked_facts(conn, page_id):
        wanted = [old[w] for w in word_ids if w in old]
        found = _same_words(wanted, value, new) if len(wanted) == len(word_ids) else None
        if found is None:
            lost.append(fact_id)
        else:
            moved[fact_id] = (found, extract.box(found, polygons, page.width, page.height) if placed else None)
            logged.append((fact_id, value, word_ids, found))
    await repo.settle_facts(conn, page_id, moved)
    if logged:
        await log_remaps(conn, case_id, logged)
    if lost:
        await flag_for_recheck(conn, case_id, lost)


async def _read_words(ctx: JobContext, services: PipelineServices) -> None:
    async with ctx.transaction() as conn:
        waiting = await repo.pages_without_words(conn, ctx.case_id)
        case_pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        spoken, locale = case_pack.language, case_pack.locale
        numbers = await repo.page_numbers_by_id(conn, ctx.case_id)
    run = steplog.current()

    async def read(page_id: uuid.UUID) -> None:
        # Short transaction to read, no connection while S3 or Azure answers, short transaction to write.
        async with ctx.transaction() as conn:
            state = await repo.reading_state(conn, page_id)
            before = await _opened_words(conn, services, ctx.case_id, page_id)
            typing = None
            if state.typed is not None:
                typing = await services.crypto.decrypt(
                    conn, ctx.case_id, state.typed, purpose=transcription_purpose(page_id)
                )
        page_pdf = None
        if typing is None:
            path = paths.page_pdf_path(ctx.case_id, page_id)
            blob = await services.storage.get(path)
            async with ctx.transaction() as conn:
                page_pdf = await services.crypto.decrypt(conn, ctx.case_id, blob, purpose=path)
        straightened = False
        if page_pdf is None:
            page, source = typed((typing or b"").decode()), "expert"
        else:
            found: OcrPage | None = await asyncio.get_running_loop().run_in_executor(
                _PDFIUM, usable_text_layer, page_pdf
            )
            source = "embedded"
            if found is None:
                if services.ocr is None:
                    raise ProcessingPaused("model_unavailable")
                try:
                    found = (await services.ocr.analyse(page_pdf, "layout", locale=locale)).pages[0]
                except OcrFailed as exc:  # F4: this page alone is marked unreadable; the rest carry on
                    if run is not None:
                        run.page_failed(numbers.get(page_id), exc, "failed")
                    found = OcrPage(number=1, width=1.0, height=1.0, words=())
                except ServiceBusy as exc:  # F5: back-off spent; the job waits and is retried later
                    if run is not None:
                        run.page_failed(numbers.get(page_id), exc, "paused")
                    raise ProcessingPaused("service_busy") from None
                source = "ocr"
            turn = state.rotation
            if source == "ocr" and not before and not state.rotation and not state.straightened:
                turn = quarter_turn(found.angle)  # first reading of a scan: store it the right way up
                straightened = turn != 0
            page = turned(found, turn)
        reading = assess(page, source)
        if straightened and page_pdf is not None:
            pictures = await _pictures(page_pdf, turn)
            thumbnail_path = paths.thumbnail_path(ctx.case_id, page_id)
            image_path = paths.page_image_path(ctx.case_id, page_id)
            for path, blob in await _seal_all(
                ctx, services, [(thumbnail_path, pictures.thumbnail), (image_path, pictures.image)]
            ):
                await services.storage.put(path, blob)  # S3 with no connection held
        async with ctx.transaction() as conn:
            rows: list[repo.WordRow] = []
            for n, word in enumerate(page.words, start=1):
                sealed = await services.crypto.encrypt(
                    conn, ctx.case_id, word.text.encode(), purpose=paths.word_purpose(page_id, word.id)
                )
                polygon = list(word.polygon)
                rows.append(
                    (
                        page_id,
                        ctx.case_id,
                        n,
                        word.id,
                        sealed,
                        polygon,
                        word.confidence,
                        word.handwritten,
                        word.struck,
                        source,
                    )
                )
            if straightened and page_pdf is not None:
                await repo.set_straightened(conn, page_id, turn)
            await repo.store_words(
                conn,
                page_id,
                rows,
                source=source,
                confidence=reading.confidence,
                handwriting=reading.handwriting or state.handwriting,
                unreadable=reading.unreadable or (state.unreadable and source != "expert"),
                size=None if source == "expert" else (page.width, page.height),
            )
            await repo.set_language(conn, page_id, language.detect(" ".join(w.text for w in page.words), spoken))
            if before:
                await _settle(conn, ctx.case_id, page_id, before, page, placed=source != "expert")

    await bounded(waiting, read, OCR_CONCURRENCY, _stop(ctx), label=lambda page_id: numbers.get(page_id))

    async def name_language(page_id: uuid.UUID) -> None:
        async with ctx.transaction() as conn:
            stored = await repo.words_of(conn, page_id)
            text = " ".join(
                [
                    (
                        await services.crypto.decrypt(
                            conn, ctx.case_id, w.sealed, purpose=paths.word_purpose(page_id, w.word_id)
                        )
                    ).decode()
                    for w in stored
                ]
            )
            await repo.set_language(conn, page_id, language.detect(text, spoken))

    async with ctx.transaction() as conn:
        unnamed = await repo.pages_without_language(conn, ctx.case_id, language.MIN_WORDS)
    await bounded(unnamed, name_language, DB_CONCURRENCY, _stop(ctx))


async def _ask(
    ctx: JobContext,
    services: PipelineServices,
    task: Any,
    pack: Any,
    who: Any,
    text: str,
    subject: str,
    provider: str | None = None,
    *,
    cut_off_raises: bool = False,
) -> dict[str, Any] | None:
    """One model call (a page or a batch of pages). Uses ``run_detached`` so no pool connection
    is held during slot waits or the model call itself. `provider="bedrock"` asks Bedrock alone.
    An answer cut off at the output cap returns None, or raises `OutputTruncated` with `cut_off_raises`."""
    assert services.kit is not None
    try:
        return await services.kit.run_detached(
            ctx.database,
            ctx.case_id,
            task,
            pack=pack,
            text=text,
            names=who.names,
            dates_of_birth=who.dates_of_birth,
            subject=subject,
            job_id=ctx.job_id,
            provider=provider,
        )
    except OutputTruncated:
        if cut_off_raises:
            raise  # the caller makes the call smaller or wider (`asking.answer_group`)
        return None
    except InvalidOutput:
        return None


@dataclass(frozen=True)
class _LoadedPage:
    stored: list[Any]
    size: tuple[float, float] | None
    latest: Any
    texts: dict[str, str]
    ordered: list[tuple[str, str]]
    struck: set[str]
    text: str
    subject: str


async def _load_page(
    ctx: JobContext, services: PipelineServices, pack: Any, who: Any, page_id: uuid.UUID
) -> _LoadedPage:
    """A page's words opened, and the identifier-stripped one-word-per-line text T-07b is asked about."""
    async with ctx.transaction() as conn:
        stored = await repo.words_of(conn, page_id)
        size = await repo.page_size(conn, page_id)
        latest = await latest_printed_date(conn, ctx.case_id)
        texts = {
            w.word_id: (
                await services.crypto.decrypt(
                    conn, ctx.case_id, w.sealed, purpose=paths.word_purpose(page_id, w.word_id)
                )
            ).decode()
            for w in stored
        }
        header = ""
        before = await repo.previous_page_words(conn, page_id, extract.HEADER_WORDS)
        if before is not None:
            before_id, top = before
            header = " ".join(
                [
                    (
                        await services.crypto.decrypt(
                            conn, ctx.case_id, w.sealed, purpose=paths.word_purpose(before_id, w.word_id)
                        )
                    ).decode()
                    for w in top
                ]
            )
    ordered = [(w.word_id, texts[w.word_id]) for w in stored]
    struck = {w.word_id for w in stored if w.struck}
    stripped = pii.strip(extract.plain_text(ordered), pack=pack, names=who.names, dates_of_birth=who.dates_of_birth)
    text = extract.page_text(ordered, stripped.text) if ordered else ""
    if header:
        cap = pii.strip(header, pack=pack, names=who.names, dates_of_birth=who.dates_of_birth)
        text = extract.with_header(text, cap.text)
    return _LoadedPage(stored, size, latest, texts, ordered, struck, text, page_subject(ordered))


def _page_anchor(found: Sequence[dates.Printed], latest: date | None) -> date:
    """The date a two-digit year is read back from: the latest date printed in full in the bundle so far."""
    return dates.bundle_anchor([*found, *([dates.Printed((), latest, None, False)] if latest is not None else [])])


def _entries_of(
    output: Mapping[str, Any] | None,
    ordered: list[tuple[str, str]],
    struck: set[str],
    pack: Any,
    dates_of_birth: Sequence[date] = (),
    lines: Mapping[str, int] | None = None,
) -> tuple[list[repo.NewEntry], int]:
    """The page's entries that pass provenance (overlaps trimmed), an entry that holds two or more lines that
    start with different printed dates split into one entry per dated line, each dated from the dates printed
    inside it (never a birth date, recorded or printed after a birth label), and the count dropped. A two-digit
    year is never read in a century here: the bundle's latest date is not known while pages are read in any
    order, so the entry stays `please_check` and the repair job reads it with the whole bundle's."""
    positions = {word_id: n for n, (word_id, _) in enumerate(ordered, start=1)}
    sorted_entries = extract.sort_entries(output, positions, pack)
    entries = []
    for whole in sorted_entries.kept:
        for e in extract.split_at_dated_lines(whole, ordered, struck, lines, pack, dates_of_birth):
            when, basis = extract.entry_date(e, ordered, struck, pack, dates_of_birth, lines, len(ordered))
            entries.append(
                repo.NewEntry(
                    kind=e.kind,
                    first_n=e.first_n,
                    last_n=e.last_n,
                    author_role=e.author_role,
                    specialty=e.specialty,
                    event_date=when,
                    date_basis=basis,
                )
            )
    return entries, sorted_entries.rejected


async def _extract(ctx: JobContext, services: PipelineServices) -> int:
    """Extracts every page waiting for it, in batches of up to `extract.pages_per_call` pages sent to
    the model in one call (one call per batch, not per page, to stay inside the account-wide Bedrock
    on-demand throughput ceiling). Returns how many pages were extracted.

    A batch is asked at most twice as a whole (`asking.answer_group`). When the answer is empty it is split
    and asked in halves; when it leaves pages out, just those pages are asked again on their own, so a page
    is only marked `invalid_output` after being asked about alone (or in a small group).

    A page with enough words but little or nothing kept (`coverage.reason_for`'s `no_facts` or
    `low_coverage`) is asked once more, on its own; the second answer replaces the first only when
    it keeps strictly more facts and is not itself refused.
    """
    async with ctx.transaction() as conn:
        await repo.reset_undone(conn, ctx.case_id)
        waiting = await repo.pages_to_extract(conn, ctx.case_id)
        if not waiting:
            return 0
        pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        who = await services.identity(conn, ctx.case_id)
    if services.kit is None:
        raise ProcessingPaused("model_unavailable")
    limits = services.kit.primary_limits  # the first-choice provider's own ceilings
    task = extract.task_for(pack, limits.max_output_tokens)
    bedrock_task = extract.task_for(pack)  # a re-split call goes to Bedrock, whose answer limit is the default
    model_id = model_for(services.kit.settings, task.model)
    size = extract.pages_per_call(services.kit.settings.extraction_batch_size, limits.pages_per_call)
    entry_floor = services.kit.settings.entry_coverage_floor
    batches = [waiting[i : i + size] for i in range(0, len(waiting), size)]
    done = 0

    async def one_batch(batch: Sequence[tuple[uuid.UUID, int]]) -> None:
        nonlocal done
        loaded: dict[uuid.UUID, _LoadedPage] = {}

        async def load(item: tuple[uuid.UUID, int]) -> None:
            loaded[item[0]] = await _load_page(ctx, services, pack, who, item[0])

        await bounded(list(batch), load, stop=_stop(ctx))
        askable = [(page_id, page_no) for page_id, page_no in batch if loaded[page_id].stored]
        answers: dict[int, Mapping[str, Any]] = {}
        asked = False

        async def ask_pages(group: Sequence[tuple[uuid.UUID, int]]) -> dict[int, Mapping[str, Any]]:
            """One call for the group, asked twice if a page failed provenance. A group with no usable answer
            at all (cut off at the output limit) is split in half and each half asked again, down to single
            pages, so one oversized answer never costs the pages their extraction."""
            # A lone page keeps its own subject (`_load_page`'s `page_subject(ordered)`), so a batch of
            # one page can still replay a recording made before batching existed; a real batch's subject
            # is the hash of its pages' own subjects, in order.
            subject = (
                loaded[group[0][0]].subject
                if len(group) == 1
                else page_subject((str(page_no), loaded[page_id].subject) for page_id, page_no in group)
            )

            def subject_of(part: Sequence[tuple[uuid.UUID, int]]) -> str:
                # A smaller group (a split, or the pages left out) keeps the same rules: a lone page its own subject.
                if len(part) == 1:
                    return loaded[part[0][0]].subject
                if len(part) == len(group):
                    return subject
                return page_subject((str(no), loaded[pid].subject) for pid, no in part)

            def text_of(part: Sequence[tuple[uuid.UUID, int]]) -> str:
                return extract.batch_text([(no, loaded[pid].text) for pid, no in part])

            async def ask_bedrock(part: Sequence[tuple[uuid.UUID, int]], fits: int) -> Mapping[str, Any] | None:
                """The direct API failed for this part and Bedrock cannot take a call this big: only this part is
                asked of Bedrock, in groups it can, and what the other parts already gave is kept."""

                async def on_bedrock(sub: Sequence[tuple[uuid.UUID, int]]) -> Mapping[str, Any] | None:
                    return await _ask(
                        ctx, services, bedrock_task, pack, who, text_of(sub), subject_of(sub), "bedrock",
                        cut_off_raises=True,
                    )  # fmt: skip

                found: dict[int, Mapping[str, Any]] = {}
                size_ = max(1, fits)
                for i in range(0, len(part), size_):
                    found.update(await answer_group(part[i : i + size_], on_bedrock, refused))
                return {"pages": list(found.values())} if found else None

            async def ask_group(part: Sequence[tuple[uuid.UUID, int]]) -> Mapping[str, Any] | None:
                text, part_subject = text_of(part), subject_of(part)
                try:
                    try:
                        return await _ask(ctx, services, task, pack, who, text, part_subject, None, cut_off_raises=True)
                    except OutputTruncated:
                        # A page cut off on its own is asked once with the provider's wider cap (and, if it is
                        # still cut off, given up by `answer_group`); a group is split by `answer_group`.
                        wider = limits.widest_output_tokens
                        if len(part) > 1 or wider <= task.max_tokens:
                            raise
                        return await _ask(
                            ctx, services, replace(task, max_tokens=wider), pack, who, text, part_subject, None,
                            cut_off_raises=True,
                        )  # fmt: skip
                except TooLargeForFallback as too_large:
                    return await ask_bedrock(part, too_large.pages_per_call)

            def refused(page: tuple[uuid.UUID, int], answer: Mapping[str, Any]) -> bool:
                return bool(extract.sort_facts(answer, loaded[page[0]].texts, loaded[page[0]].struck).refused)

            return await answer_group(group, ask_group, refused)

        if askable:
            asked = True
            answers = await ask_pages(askable)
        for page_id, page_no in batch:
            loaded_page = loaded[page_id]
            stored, size_, latest, texts = (
                loaded_page.stored,
                loaded_page.size,
                loaded_page.latest,
                loaded_page.texts,
            )
            ordered, struck = loaded_page.ordered, loaded_page.struck
            raw_output = answers.get(page_no) if stored else None
            output = dict(raw_output) if raw_output is not None else None
            kept: list[Any] = []
            reason: str | None = None
            if not stored:  # a page OCR could not read (F4) has nothing to ask the model about
                reason = "no_words"
            elif output is None:
                reason = "invalid_output" if asked else "no_words"
            else:
                result = extract.sort_facts(output, texts, struck)
                kept, reason = result.kept, ("not_in_words" if result.refused else None)
            found = dates.find([(w, t) for w, t in ordered if w not in struck], pack)
            polygons = {w.word_id: w.polygon for w in stored}
            width, height = size_ or (
                max((x for w in stored for x in w.polygon[0::2]), default=1.0) or 1.0,
                max((y for w in stored for y in w.polygon[1::2]), default=1.0) or 1.0,
            )
            evidence = extract.sort_evidence(output, ordered, struck, pack)
            lines = dates.line_numbers([(w.word_id, w.polygon) for w in stored])
            entries: list[Any] | None
            anchor = _page_anchor(found, latest)
            entries, entries_rejected = _entries_of(output, ordered, struck, pack, who.dates_of_birth, lines)
            if stored and output is None:
                entries = None  # no usable answer: not "no entries"; the page is left for a person
            if reason is None and stored and output is not None:
                reason = coverage.reason_for(ordered=ordered, struck=struck, kept=kept, entries=entries)
            thin_entries = (
                reason is None
                and entries is not None
                and coverage.entry_reason(
                    ordered=ordered,
                    struck=struck,
                    entries=entries,
                    floor=entry_floor,
                )
                is not None
            )
            if reason in ("no_facts", "low_coverage") or thin_entries:  # little to show: ask once more, on its own
                again_text = extract.batch_text([(page_no, loaded_page.text)])
                again_raw = await _ask(ctx, services, task, pack, who, again_text, loaded_page.subject)
                again = extract.pages_of(again_raw).get(page_no) if again_raw is not None else None
                if again is not None:
                    again_result = extract.sort_facts(again, texts, struck)
                    again_entries, again_rejected = _entries_of(again, ordered, struck, pack, who.dates_of_birth, lines)
                    more_facts = len(again_result.kept) > len(kept)
                    wider = len(again_result.kept) >= len(kept) and coverage.entry_coverage(
                        ordered=ordered, struck=struck, entries=again_entries
                    ) > coverage.entry_coverage(ordered=ordered, struck=struck, entries=entries or [])
                    if not again_result.refused and (more_facts or (thin_entries and wider)):
                        output, kept = dict(again), again_result.kept
                        evidence = extract.sort_evidence(output, ordered, struck, pack)
                        entries, entries_rejected = again_entries, again_rejected
                        reason = coverage.reason_for(ordered=ordered, struck=struck, kept=kept, entries=entries)
            facts = []
            for f in kept:
                fields = dict(f.get("fields") or {})
                when, basis, extra = date_repair.resolve_dated(
                    str(f["kind"]), fields, f["word_ids"], ordered, struck, lines, pack, anchor=anchor
                )
                facts.append(
                    repo.NewFact(
                        kind=str(f["kind"]),
                        fields=fields,
                        value=str(f["value"]),
                        word_ids=(
                            date_repair.cited_with(f["word_ids"], extra, ordered) if extra else list(f["word_ids"])
                        ),
                        bbox=extract.box(f["word_ids"], polygons, width, height),
                        event_date=when,
                        date_basis=basis,
                    )
                )
            async with ctx.transaction() as conn:
                sealed = None
                if output is not None:
                    sealed = await services.crypto.encrypt(
                        conn, ctx.case_id, json.dumps(output).encode(), purpose=paths.extraction_purpose(page_id)
                    )
                await repo.store_extraction(
                    conn,
                    ctx.case_id,
                    page_id,
                    source_type=output.get("source_type") if output else None,
                    facts=facts,
                    sealed=sealed,
                    needs_human=reason is not None,
                    reason=reason,
                    task=task.task_id,
                    model_id=model_id,
                    prompt_version=task.prompt_version,
                    printed_dates=dates.printed_dates(found),
                    availability_evidence=evidence,
                    entries=entries,
                    entries_rejected=entries_rejected,
                )
            done += 1
            if done % 25 == 0:
                await ctx.progress(
                    min(55, 40 + 15 * done // len(waiting)), step="sort", step_done=done, step_total=len(waiting)
                )

    await bounded(batches, one_batch, _extract_concurrency(ctx, services), _stop(ctx))
    await ctx.progress(55, step="sort", step_done=len(waiting), step_total=len(waiting))
    return len(waiting)


async def _suggest_quarantine(ctx: JobContext, services: PipelineServices) -> None:
    """Suggests the pages whose identifiers are not the case subject's (F25). Never hides a page; the
    identifiers are compared in memory and never stored."""
    async with ctx.transaction() as conn:
        candidates = await repo.pages_for_identity(conn, ctx.case_id)
        pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        who = await services.identity(conn, ctx.case_id)
    found: dict[uuid.UUID, dict[str, set[str]]] = {}

    async def one(page_id: uuid.UUID) -> None:
        async with ctx.transaction() as conn:
            stored = await repo.words_of(conn, page_id)
            ordered = [
                (
                    w.word_id,
                    (
                        await services.crypto.decrypt(
                            conn, ctx.case_id, w.sealed, purpose=paths.word_purpose(page_id, w.word_id)
                        )
                    ).decode(),
                )
                for w in stored
                if not w.struck
            ]
        found[page_id] = quarantine.identifiers(extract.plain_text(ordered), pack)

    await bounded(candidates, one, DB_CONCURRENCY, _stop(ctx))
    flagged = quarantine.suggest({p: found[p] for p in candidates}, quarantine.known(who.dates_of_birth))
    if flagged:
        async with ctx.transaction() as conn:
            await repo.add_suggestions(conn, ctx.case_id, flagged, quarantine.REASON)


async def _sort(ctx: JobContext, services: PipelineServices) -> None:
    """Extraction, then (when any page was extracted) the wrong-person check across the bundle."""
    if await _extract(ctx, services):
        await _suggest_quarantine(ctx, services)


async def _compared_words(
    conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, page_id: uuid.UUID
) -> list[duplicates.Word]:
    stored = await repo.words_of(conn, page_id)
    texts = [
        (
            await services.crypto.decrypt(conn, case_id, w.sealed, purpose=paths.word_purpose(page_id, w.word_id))
        ).decode()
        for w in stored
    ]
    return duplicates.tokens([(t, w.handwritten, w.struck) for t, w in zip(texts, stored, strict=True)])


async def _duplicates(ctx: JobContext, services: PipelineServices) -> None:
    async with ctx.transaction() as conn:
        unhashed = await repo.pages_to_hash(conn, ctx.case_id)
        pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        numbers = await repo.page_numbers_by_id(conn, ctx.case_id)

    async def hash_one(page_id: uuid.UUID) -> None:
        path = paths.thumbnail_path(ctx.case_id, page_id)
        blob = await services.storage.get(path)  # S3 with no connection held
        async with ctx.transaction() as conn:
            words = await _compared_words(conn, services, ctx.case_id, page_id)
            thumbnail = await services.crypto.decrypt(conn, ctx.case_id, blob, purpose=path)
        picture = await asyncio.get_running_loop().run_in_executor(_CPU, duplicates.picture_hash, thumbnail)
        typed = sum(1 for w in words if not w.handwritten)
        async with ctx.transaction() as conn:
            await repo.store_hashes(conn, page_id, picture, duplicates.word_hash(words), typed)

    await bounded(unhashed, hash_one, STORAGE_CONCURRENCY, _stop(ctx), label=lambda page_id: numbers.get(page_id))
    async with ctx.transaction() as conn:
        states = await repo.copy_states(conn, ctx.case_id)
    new: set[object] = {s.page_id for s in states if not s.checked}
    if not new:
        return
    signatures = [
        duplicates.Signature(s.page_id, s.page_no, s.picture, s.words, s.typed_words)
        for s in states
        if s.picture is not None and s.words is not None
    ]
    pairs = duplicates.likely_pairs(signatures, new=new)
    words: dict[object, list[duplicates.Word]] = {}

    async def load(page_id: uuid.UUID) -> None:
        async with ctx.transaction() as conn:
            words[page_id] = await _compared_words(conn, services, ctx.case_id, page_id)

    await bounded(sorted({p.page_id for pair in pairs for p in pair}, key=str), load, DB_CONCURRENCY)  # type: ignore[arg-type]
    common = duplicates.edge_runs(words.values())  # a header or footer is a run that starts or ends several pages
    same = [
        (a.page_id, b.page_id)
        for a, b in pairs
        if duplicates.same_content(words[a.page_id], words[b.page_id], pack, common)
    ]
    found = duplicates.originals(
        same,
        {s.page_id: s.page_no for s in states},
        copy_of={s.page_id: s.copy_of for s in states},
        new=new,
        kept={s.page_id for s in states if s.status == "kept"},
    )
    async with ctx.transaction() as conn:
        await repo.set_aside(conn, {uuid.UUID(str(k)): uuid.UUID(str(v)) for k, v in found.items()})
        await repo.mark_checked(conn, [uuid.UUID(str(p)) for p in new])


async def _gaps(ctx: JobContext, services: PipelineServices) -> None:
    """Where a record source goes quiet: worked out again from every page each run (code only)."""
    async with ctx.transaction() as conn:
        rules = gaps.Rules.from_pack(services.packs.for_case(await repo.case_codes(conn, ctx.case_id)))
        await repo.store_gaps(conn, ctx.case_id, gaps.find(await repo.gap_pages(conn, ctx.case_id), rules))


async def _missing(ctx: JobContext, services: PipelineServices) -> None:
    """T-07c: each document a page mentions, asked once, with only the reference's own words."""
    async with ctx.transaction() as conn:
        waiting = await repo.pages_to_match(conn, ctx.case_id)
        if not waiting:
            return
        pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        who = await services.identity(conn, ctx.case_id)
        pages = await repo.gap_pages(conn, ctx.case_id)
        numbers = await repo.page_numbers(conn, ctx.case_id)
    if services.kit is None:
        raise ProcessingPaused("model_unavailable")
    task = missing.task_for(pack)

    async def one(page: tuple[uuid.UUID, int]) -> None:
        page_id, page_no = page
        async with ctx.transaction() as conn:
            sealed = await repo.sealed_extraction(conn, page_id)
            output: dict[str, Any] = {}
            if sealed is not None:
                output = json.loads(
                    await services.crypto.decrypt(conn, ctx.case_id, sealed, purpose=paths.extraction_purpose(page_id))
                )
            stored = await repo.words_of(conn, page_id)
            texts = {
                w.word_id: (
                    await services.crypto.decrypt(
                        conn, ctx.case_id, w.sealed, purpose=paths.word_purpose(page_id, w.word_id)
                    )
                ).decode()
                for w in stored
            }
        ordered = [(w.word_id, texts[w.word_id]) for w in stored]
        struck = {w.word_id for w in stored if w.struck}
        found = dates.find([(w, t) for w, t in ordered if w not in struck], pack)
        subject_page = page_subject(ordered)
        items: list[repo.NewMissing] = []
        for k, ref in enumerate(output.get("references_out") or [], start=1):
            word_ids = [str(w) for w in ref.get("word_ids") or []]
            cited = [texts[w] for w in word_ids if w in texts]
            if not word_ids or len(cited) != len(word_ids):
                continue
            when, basis = dates.event_date(ref.get("date"), found, pack)
            ref_date = when if basis == "printed" else None
            shown = missing.candidates(pages, ref_date, page_no)
            text = missing.request_text(f"p{page_no}-r{k}", cited, ref_date, shown)
            answer = await _ask(ctx, services, task, pack, who, text, reference_subject(subject_page, ref["word_ids"]))
            if answer is None:
                continue
            matched = answer.get("matched_page")
            if matched is not None and int(matched) in numbers:
                continue
            item_id = uuid.uuid4()
            words = missing.description(str(ref.get("described_document") or ""), cited)
            async with ctx.transaction() as conn:
                sealed_words = await services.crypto.encrypt(
                    conn, ctx.case_id, words.encode(), purpose=paths.missing_description_purpose(item_id)
                )
            items.append(
                repo.NewMissing(
                    id=item_id,
                    reference_key=f"r{k}",
                    word_ids=word_ids,
                    sealed=sealed_words,
                    reference_date=ref_date,
                    confidence=float(answer["confidence"]) if answer.get("confidence") is not None else None,
                )
            )
        async with ctx.transaction() as conn:
            await repo.add_missing(conn, ctx.case_id, page_id, items)

    await bounded(waiting, one, CONCURRENCY, _stop(ctx), label=lambda item: item[1])


async def _missing_step(ctx: JobContext, services: PipelineServices) -> None:
    await _gaps(ctx, services)
    await _missing(ctx, services)


async def _embed(ctx: JobContext, services: PipelineServices) -> None:
    """Embeds each page not yet embedded by the cell's embedder, from its text with identifiers
    stripped (rule 7). A page embedded by an older model is embedded again."""
    embedder = services.embedder
    async with ctx.transaction() as conn:
        waiting = await repo.pages_to_embed(conn, ctx.case_id, embedder.model)
        if not waiting:
            return
        pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        who = await services.identity(conn, ctx.case_id)

    async def prepare(page_id: uuid.UUID) -> pii.Stripped:
        async with ctx.transaction() as conn:
            stored = await repo.words_of(conn, page_id)
            ordered = [
                (
                    w.word_id,
                    (
                        await services.crypto.decrypt(
                            conn, ctx.case_id, w.sealed, purpose=paths.word_purpose(page_id, w.word_id)
                        )
                    ).decode(),
                )
                for w in stored
            ]
        return pii.strip(extract.plain_text(ordered), pack=pack, names=who.names, dates_of_birth=who.dates_of_birth)

    texts: dict[uuid.UUID, pii.Stripped] = {}

    async def read(page_id: uuid.UUID) -> None:
        texts[page_id] = await prepare(page_id)

    for start in range(0, len(waiting), embed.BATCH):
        chunk = waiting[start : start + embed.BATCH]
        texts.clear()
        await bounded(chunk, read, DB_CONCURRENCY, _stop(ctx))
        ready = [page_id for page_id in chunk if page_id in texts]
        vectors = await embed.embed_batches(embedder, [texts[page_id] for page_id in ready])
        async with ctx.transaction() as conn:
            for page_id, vector in zip(ready, vectors, strict=True):
                await repo.store_embedding(conn, ctx.case_id, page_id, embedder.model, vector)


async def _ready(ctx: JobContext, services: PipelineServices) -> None:
    """Pages split in this job are ready: the case leaves `processing` and the named expert is told
    once (the kind only, never case text)."""
    async with ctx.transaction() as conn:
        if not await repo.split_in_job(conn, ctx.case_id, ctx.job_id):
            return
        await repo.activate_case(conn, ctx.case_id)
        expert, organisation = await repo.case_people(conn, ctx.case_id)
        await notifications.notify(
            conn, services.mailer, person_id=expert, organisation_id=organisation, kind=READY, case_id=ctx.case_id
        )


STEP_CONCURRENCY = {
    "split": STORAGE_CONCURRENCY,
    "ocr": OCR_CONCURRENCY,
    "sort": CONCURRENCY,
    "duplicates": STORAGE_CONCURRENCY,
    "missing": CONCURRENCY,
    "embed": DB_CONCURRENCY,
}


def _extract_concurrency(ctx: JobContext, services: PipelineServices) -> int:
    """Calls to the model in flight at once: the provider's own limit, never more than the worker's pool
    less a small reserve (and always at least one)."""
    room = max(1, ctx.database.max_size - POOL_RESERVE)
    return min(services.kit.primary_limits.concurrency, room) if services.kit is not None else min(CONCURRENCY, room)


def _pending(services: PipelineServices, step: str) -> steplog.Pending:
    """What a step has to do when it starts: (unit, to do, already done), for its start line."""

    async def count(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[str, int, int]:
        if step == "split":
            return "documents", len(await repo.clean_unsplit(conn, case_id)), 0
        waiting: dict[str, Callable[[AsyncConnection, uuid.UUID], Awaitable[Sequence[object]]]] = {
            "ocr": repo.pages_without_words,
            "sort": repo.pages_to_extract,
            "duplicates": repo.pages_to_hash,
            "missing": repo.pages_to_match,
        }
        if step == "embed":
            todo = len(await repo.pages_to_embed(conn, case_id, services.embedder.model))
        else:
            todo = len(await waiting[step](conn, case_id))
        return "pages", todo, max(0, len(await repo.page_numbers(conn, case_id)) - todo)

    return count


async def run_step(ctx: JobContext, services: PipelineServices, step: str) -> None:
    """One step of the bundle job, with its start and end lines (steplog)."""
    stages: dict[str, Callable[[JobContext, PipelineServices], Awaitable[None]]] = {
        "split": _split,
        "ocr": _read_words,
        "sort": _sort,
        "duplicates": _duplicates,
        "missing": _missing_step,
        "embed": _embed,
    }
    concurrency = _extract_concurrency(ctx, services) if step == "sort" else STEP_CONCURRENCY[step]  # as logged
    async with steplog.logged_step(ctx, step, _pending(services, step), concurrency):
        await stages[step](ctx, services)


async def process_bundle(ctx: JobContext) -> None:
    services = current()
    starts = {"split": 0, "ocr": 30, "sort": 40, "duplicates": 55, "missing": 70}
    for step in STEPS:
        await ctx.progress(starts[step], step=step)
        await run_step(ctx, services, step)
        await ctx.step(step, _noop)
    await run_step(ctx, services, "embed")
    await _ready(ctx, services)
    await ctx.progress(100, step=STEPS[-1])


async def reprocess_case(ctx: JobContext) -> None:
    """Extracts again every page extracted before the expert opted in (`POST /reprocess`). Words are
    not read again; verified facts never change; new suggestions carry the current model ID."""
    services = current()
    async with ctx.transaction() as conn:
        await repo.reset_for_reprocess(conn, ctx.case_id)
    await ctx.progress(40, step="sort")
    await _sort(ctx, services)
    await ctx.progress(100, step="sort")


async def retry_unread_pages(ctx: JobContext) -> None:
    """Extracts again only the pages the model could not read (`POST /transcript/retry`, summary button).

    Their failed marker is cleared and they are extracted the way they were first; pages that read well and
    every verified fact are left alone. A page that fails again is left for a person, and nothing asks again."""
    services = current()
    async with ctx.transaction() as conn:
        await repo.reset_unread(conn, ctx.case_id, ctx.job_id)
    await ctx.progress(40, step="sort")
    await _sort(ctx, services)
    await ctx.progress(100, step="sort")


async def extract_entries(ctx: JobContext) -> None:
    """Reads entries for pages that have none yet; facts and the page's notes are left as they are.

    Idempotent: a page whose entries are read is skipped, so a rerun asks the model about nothing."""
    services = current()
    async with ctx.transaction() as conn:
        waiting = await repo.pages_to_read_entries(conn, ctx.case_id)
        if not waiting:
            await ctx.progress(100, step="entries")
            return
        pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        who = await services.identity(conn, ctx.case_id)
    if services.kit is None:
        raise ProcessingPaused("model_unavailable")
    task = extract.task_for(pack)
    done = 0

    async def one(page_id: uuid.UUID) -> None:
        nonlocal done
        loaded = await _load_page(ctx, services, pack, who, page_id)
        # The kit asks twice itself before refusing, so a None here is the settled "no usable answer".
        raw = (
            await _ask(ctx, services, task, pack, who, extract.batch_text([(1, loaded.text)]), loaded.subject)
            if loaded.stored
            else None
        )
        answer = extract.pages_of(raw).get(1) if raw is not None else None
        output = dict(answer) if answer is not None else None
        lines = dates.line_numbers([(w.word_id, w.polygon) for w in loaded.stored])
        entries, rejected = _entries_of(output, loaded.ordered, loaded.struck, pack, who.dates_of_birth, lines)
        async with ctx.transaction() as conn:
            if loaded.stored and output is None:
                await repo.leave_entries_for_person(conn, page_id)
            else:
                await repo.store_entries(
                    conn, ctx.case_id, page_id, entries, rejected=rejected, version=task.prompt_version
                )
        done += 1
        if done % 25 == 0:
            await ctx.progress(
                min(95, 95 * done // len(waiting)), step="entries", step_done=done, step_total=len(waiting)
            )

    await bounded(waiting, one, stop=_stop(ctx))
    await ctx.progress(100, step="entries", step_done=len(waiting), step_total=len(waiting))


async def repair_fact_dates(ctx: JobContext) -> None:
    """Sets `event_date` on facts and entries that have none, using the same printed-word rules as extraction.

    An entry that has a date of its own is left alone; one dated with a birth date (recorded, or printed after a
    birth label) is dated again, and a card that holds two or more lines starting with different dates is split
    into one entry per dated line. A birth date is never used as an entry's date."""
    services = current()
    async with ctx.transaction() as conn:
        pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        rows = await repo.undated_facts(conn, ctx.case_id)
        entry_rows = await repo.undated_entries(conn, ctx.case_id)
        anchor = await case_anchor(conn, ctx.case_id)
        who = await services.identity(conn, ctx.case_id)
    if not rows and not entry_rows:
        await ctx.progress(100, step="repair")
        return
    by_page: dict[uuid.UUID, tuple[list[repo.UndatedFact], list[repo.UndatedEntry]]] = {}
    for row in rows:
        by_page.setdefault(row.page_id, ([], []))[0].append(row)
    for entry_row in entry_rows:
        by_page.setdefault(entry_row.page_id, ([], []))[1].append(entry_row)
    done = 0
    repaired = 0
    total = len(rows) + len(entry_rows)

    async def one_page(item: tuple[uuid.UUID, tuple[list[repo.UndatedFact], list[repo.UndatedEntry]]]) -> None:
        nonlocal done, repaired
        page_id, (facts, undated) = item
        async with ctx.transaction() as conn:
            stored = await repo.words_of(conn, page_id)
            if not stored:
                done += len(facts) + len(undated)
                return
            texts = {
                w.word_id: (
                    await services.crypto.decrypt(
                        conn, ctx.case_id, w.sealed, purpose=paths.word_purpose(page_id, w.word_id)
                    )
                ).decode()
                for w in stored
            }
            ranges = await repo.entry_ranges(conn, page_id)
        ordered = [(w.word_id, texts[w.word_id]) for w in stored]
        struck = {w.word_id for w in stored if w.struck}
        lines = dates.line_numbers([(w.word_id, w.polygon) for w in stored])
        page_entries = [
            extract.Entry(
                "", ordered[min(a, len(ordered)) - 1][0], ordered[min(b, len(ordered)) - 1][0], a, b, None, None
            )
            for a, b in ranges
            if a <= len(ordered)
        ]
        async with ctx.transaction() as conn:
            for fact in facts:
                when, basis, extra = date_repair.resolve_dated(
                    fact.kind,
                    fact.fields,
                    fact.word_ids,
                    ordered,
                    struck,
                    lines,
                    pack,
                    anchor=anchor,
                    line_above=fact.status not in ("verified", "edited"),  # their cited words are the expert's
                )
                if when is not None and basis is not None and basis != dates.PLEASE_CHECK:
                    cited = date_repair.cited_with(fact.word_ids, extra, ordered) if extra else None
                    if await repo.set_fact_event_date(conn, ctx.case_id, fact.fact_id, when, basis, cited):
                        repaired += 1
            every, _ = extract.page_births(ordered, struck, pack, who.dates_of_birth, anchor)
            for row in undated:
                if row.first_n > len(ordered):
                    continue
                first, last = ordered[row.first_n - 1][0], ordered[min(row.last_n, len(ordered)) - 1][0]
                whole = extract.Entry(row.kind, first, last, row.first_n, row.last_n, None, None)
                birth_dated = row.event_date is not None and dates.is_birth(
                    dates.Printed((), row.event_date, None, False), every
                )
                if row.event_date is not None and not birth_dated:
                    # a dated entry is changed only when its date is a birth date, or it is a card to split
                    pieces = extract.split_at_dated_lines(
                        whole, ordered, struck, lines, pack, who.dates_of_birth, anchor
                    )
                    if len(pieces) == 1:
                        continue
                else:
                    pieces = extract.split_at_dated_lines(
                        whole, ordered, struck, lines, pack, who.dates_of_birth, anchor
                    )
                dated = [
                    extract.repair_entry_date(
                        piece,
                        ordered,
                        struck,
                        lines,
                        pack,
                        dates_of_birth=who.dates_of_birth,
                        anchor=anchor,
                        page_size=len(ordered),
                        page_entries=page_entries,
                    )
                    for piece in pieces
                ]
                if len(pieces) == 1 and row.event_date is None:  # as before: only a date found is written
                    when, basis = dated[0]
                    if when is not None and basis is not None and basis != dates.PLEASE_CHECK:
                        if await repo.set_entry_event_date(conn, row.entry_id, when, basis):
                            repaired += 1
                    continue
                await repo.replace_entry(
                    conn,
                    row.entry_id,
                    [
                        repo.NewEntry(
                            kind=row.kind, first_n=p.first_n, last_n=p.last_n, event_date=when, date_basis=basis
                        )
                        for p, (when, basis) in zip(pieces, dated, strict=True)
                    ],
                )
                repaired += 1
        done += len(facts) + len(undated)
        if done == total or done % 50 == 0:
            await ctx.progress(min(99, 100 * done // total), step="repair")

    await bounded(list(by_page.items()), one_page, DB_CONCURRENCY, _stop(ctx))
    await ctx.progress(100, step="repair")


async def queue_pages(conn: AsyncConnection, case_id: uuid.UUID, page_ids: Sequence[uuid.UUID]) -> uuid.UUID:
    """The `process_pages` entry point other slices call (core/contracts/pipeline.py): the named
    pages are read again (a new page has nothing to read again)."""
    from app.features.pipeline.wiring import registry

    await repo.read_again(conn, case_id, list(page_ids))
    return await registry().enqueue(conn, case_id, PROCESS_PAGES)


async def queue_page_text(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> uuid.UUID:
    """The `extract_page_text` entry point (core/contracts/pipeline.py): the page's words come from
    the expert's typed-out text on the next run, which also extracts it again."""
    from app.features.pipeline.wiring import registry

    if not await repo.read_typed_again(conn, case_id, page_id):
        raise LookupError("the page has no typed-out text")
    return await registry().enqueue(conn, case_id, PROCESS_PAGES)


async def purge_quarantined(conn: AsyncConnection, run_date: date) -> None:
    """Deletes the image and text of each confirmed quarantine that is due by `run_date`: the
    instructing party has been told, or the pack's `quarantine_purge_days` have passed since it was
    confirmed. Files go first, then the rows, in one transaction, so a failed run is simply run
    again."""
    services = current()
    for q in await repo.quarantines_to_purge(conn, run_date):
        days = services.packs.for_case(q.codes).quarantine_purge_days
        if not (q.told or q.confirmed_on + timedelta(days=days) <= run_date):
            continue
        await services.storage.delete(await services.storage.list(paths.page_prefix(q.case_id, q.page_id)))
        source = await repo.purge_quarantined_page(conn, q.quarantine_id)
        if source:
            await services.storage.delete(await services.storage.list_recursive(source))
