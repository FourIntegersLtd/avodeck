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
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from datetime import date, timedelta
from typing import Any

from psycopg import AsyncConnection

from app.core import audit, notifications
from app.core.ai import pii, provenance
from app.core.ai.answers import page_subject, reference_subject
from app.core.ai.cell import model_for
from app.core.ai.kit import InvalidOutput
from app.core.ai.bedrock import ServiceBusy
from app.core.ai.ocr import OcrFailed, OcrPage
from app.core.contracts.fact_flags import flag_for_recheck
from app.core.contracts.fact_verifications import log_remaps
from app.core.contracts.pipeline import PROCESS_BUNDLE, PROCESS_PAGES, transcription_purpose
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext
from app.core.pdf import split_pages
from app.features.pipeline import convert, dates, duplicates, extract, gaps, language, missing, paths, quarantine, repo
from app.features.pipeline.render import render
from app.features.pipeline.wiring import PipelineServices, current
from app.features.pipeline.words import assess, turned, typed, usable_text_layer

SCAN_DOCUMENT = "scan_document"
REPROCESS_CASE = "reprocess_case"  # the expert's opt-in to extract every page again
PURGE_QUARANTINED = "purge_quarantined_pages"  # the daily deletion of quarantined pages' images and text
CONCURRENCY = 3  # pages read or sent to a model at once
LIMITS = convert.Limits()
PART_OBJECT = "file"  # the one sealed object under a part's prefix
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
        async with ctx.transaction() as conn:
            result = await services.scanner.scan(_chunks(conn, services, ctx.case_id, doc.storage_path))
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
    conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, doc: repo.Document
) -> list[bytes]:
    """Cuts one document into pages; the files inside an email or a zip become documents of their
    own (cut on the next round) and files that cannot be paged become exhibits (F36)."""
    data = await _read_document(conn, services, case_id, doc.storage_path)
    found = convert.unpack(data, doc.filename, LIMITS)
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
    return _pages_of(found)


async def _split(ctx: JobContext, services: PipelineServices) -> None:
    cache: dict[uuid.UUID, list[bytes]] = {}
    while True:
        async with ctx.transaction() as conn:
            documents = await repo.clean_unsplit(conn, ctx.case_id)
        if not documents:
            break
        for doc in documents:
            async with ctx.transaction() as conn:
                pages = await _unpack(conn, services, ctx.case_id, doc)
                if await repo.add_pages(conn, ctx.case_id, doc.id, len(pages)):
                    cache[doc.id] = pages
                await repo.link_replacements(conn, ctx.case_id)
    async with ctx.transaction() as conn:
        waiting = await repo.pages_without_image(conn, ctx.case_id)
    stop = _stop(ctx)
    for n, page in enumerate(waiting, start=1):
        if await stop():
            raise ProcessingPaused("cancelled")
        async with ctx.transaction() as conn:
            if page.document_id not in cache:
                prefix, filename = await repo.document_file(conn, page.document_id)
                data = await _read_document(conn, services, ctx.case_id, prefix)
                cache[page.document_id] = _pages_of(convert.unpack(data, filename, LIMITS))
            page_pdf = cache[page.document_id][page.bundle_page_no - page.first_page_no]
            pictures = render(page_pdf, page.rotation)
            await _put_sealed(conn, services, ctx.case_id, paths.page_pdf_path(ctx.case_id, page.id), page_pdf)
            await _put_sealed(
                conn, services, ctx.case_id, paths.thumbnail_path(ctx.case_id, page.id), pictures.thumbnail
            )
            image_path = paths.page_image_path(ctx.case_id, page.id)
            await _put_sealed(conn, services, ctx.case_id, image_path, pictures.image)
            await repo.set_image(conn, page.id, image_path)
        if n % 25 == 0:
            await ctx.progress(min(30, 30 * n // len(waiting)), step="split")


Stop = Callable[[], Awaitable[bool]]


async def bounded[T](
    items: Sequence[T], work: Callable[[T], Awaitable[None]], limit: int = CONCURRENCY, stop: Stop | None = None
) -> None:
    """Run `work` on every item, at most `limit` at once; the first error stops the rest.

    With `stop`, it is asked before each item starts: once it says yes, no new item starts, items
    already running finish, and the job pauses with reason `cancelled`.
    """
    gate = asyncio.Semaphore(limit)
    stopped = False

    async def one(item: T) -> None:
        nonlocal stopped
        async with gate:
            if stopped or (stop is not None and await stop()):
                stopped = True
                return
            await work(item)

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
        spoken = services.packs.for_case(await repo.case_codes(conn, ctx.case_id)).language

    async def read(page_id: uuid.UUID) -> None:
        async with ctx.transaction() as conn:
            state = await repo.reading_state(conn, page_id)
            before = await _opened_words(conn, services, ctx.case_id, page_id)
            typing = None
            if state.typed is not None:
                typing = await services.crypto.decrypt(
                    conn, ctx.case_id, state.typed, purpose=transcription_purpose(page_id)
                )
            page_pdf = None if typing is not None else await _page_pdf(conn, services, ctx.case_id, page_id)
        if page_pdf is None:
            page, source = typed((typing or b"").decode()), "expert"
        else:
            found: OcrPage | None = usable_text_layer(page_pdf)
            source = "embedded"
            if found is None:
                if services.ocr is None:
                    raise ProcessingPaused("model_unavailable")
                try:
                    found = (await services.ocr.analyse(page_pdf, "layout")).pages[0]
                except OcrFailed:  # F4: this page alone is marked unreadable; the rest carry on
                    found = OcrPage(number=1, width=1.0, height=1.0, words=())
                except ServiceBusy:  # F5: back-off spent; the job waits and is retried later
                    raise ProcessingPaused("service_busy") from None
                source = "ocr"
            page = turned(found, state.rotation)
        reading = assess(page, source)
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

    await bounded(waiting, read, stop=_stop(ctx))

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
    await bounded(unnamed, name_language, stop=_stop(ctx))


async def _ask(
    ctx: JobContext, services: PipelineServices, task: Any, pack: Any, who: Any, text: str, subject: str
) -> dict[str, Any] | None:
    """One T-07b call in its own transaction, so its `ai_calls` rows stay even if the page fails."""
    assert services.kit is not None
    async with ctx.transaction() as conn:
        try:
            return await services.kit.run(
                conn,
                task,
                pack=pack,
                text=text,
                case_id=ctx.case_id,
                names=who.names,
                dates_of_birth=who.dates_of_birth,
                subject=subject,
            )
        except InvalidOutput:
            return None


async def _extract(ctx: JobContext, services: PipelineServices) -> int:
    """Extracts every page waiting for it; returns how many."""
    async with ctx.transaction() as conn:
        await repo.reset_undone(conn, ctx.case_id)
        waiting = await repo.pages_to_extract(conn, ctx.case_id)
        if not waiting:
            return 0
        pack = services.packs.for_case(await repo.case_codes(conn, ctx.case_id))
        who = await services.identity(conn, ctx.case_id)
    if services.kit is None:
        raise ProcessingPaused("model_unavailable")
    task = extract.task_for(pack)
    model_id = model_for(services.kit.settings, task.model)
    done = 0

    async def one(page_id: uuid.UUID) -> None:
        nonlocal done
        async with ctx.transaction() as conn:
            stored = await repo.words_of(conn, page_id)
            size = await repo.page_size(conn, page_id)
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
        subject = page_subject(ordered)
        output: dict[str, Any] | None = None
        kept: list[Any] = []
        reason: str | None = None
        if not stored:  # a page OCR could not read (F4) has nothing to ask the model about
            kept, reason = [], "no_words"
        for _ in range(0 if not stored else 2):
            output = await _ask(ctx, services, task, pack, who, text, subject)
            if output is None:
                kept, reason = [], "invalid_output"
                break
            result = extract.sort_facts(output, texts, struck)
            kept, reason = result.kept, ("not_in_words" if result.refused else None)
            if reason is None:
                break
        found = dates.find([(w, t) for w, t in ordered if w not in struck], pack)
        polygons = {w.word_id: w.polygon for w in stored}
        width, height = size or (
            max((x for w in stored for x in w.polygon[0::2]), default=1.0) or 1.0,
            max((y for w in stored for y in w.polygon[1::2]), default=1.0) or 1.0,
        )
        evidence = extract.sort_evidence(output, ordered, struck, pack)
        facts = []
        for f in kept:
            fields = dict(f.get("fields") or {})
            field = dates.date_field(pack, str(f["kind"]))
            when, basis = dates.event_date(fields.get(field) if field else None, found, pack)
            facts.append(
                repo.NewFact(
                    kind=str(f["kind"]),
                    fields=fields,
                    value=str(f["value"]),
                    word_ids=list(f["word_ids"]),
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
            )
        done += 1
        if done % 25 == 0:
            await ctx.progress(min(55, 40 + 15 * done // len(waiting)), step="sort")

    await bounded(waiting, one, stop=_stop(ctx))
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

    await bounded(candidates, one, stop=_stop(ctx))
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

    async def hash_one(page_id: uuid.UUID) -> None:
        async with ctx.transaction() as conn:
            words = await _compared_words(conn, services, ctx.case_id, page_id)
            path = paths.thumbnail_path(ctx.case_id, page_id)
            thumbnail = await services.crypto.decrypt(conn, ctx.case_id, await services.storage.get(path), purpose=path)
            typed = sum(1 for w in words if not w.handwritten)
            await repo.store_hashes(
                conn, page_id, duplicates.picture_hash(thumbnail), duplicates.word_hash(words), typed
            )

    await bounded(unhashed, hash_one, stop=_stop(ctx))
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

    await bounded(sorted({p.page_id for pair in pairs for p in pair}, key=str), load)  # type: ignore[arg-type]
    same = [
        (a.page_id, b.page_id) for a, b in pairs if duplicates.same_content(words[a.page_id], words[b.page_id], pack)
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

    await bounded(waiting, one, stop=_stop(ctx))


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
            ]
        stripped = pii.strip(extract.plain_text(ordered), pack=pack, names=who.names, dates_of_birth=who.dates_of_birth)
        [vector] = embedder.embed([stripped])
        async with ctx.transaction() as conn:
            await repo.store_embedding(conn, ctx.case_id, page_id, embedder.model, vector)

    await bounded(waiting, one, stop=_stop(ctx))


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


async def process_bundle(ctx: JobContext) -> None:
    services = current()
    stages: dict[str, Callable[[JobContext, PipelineServices], Awaitable[None]]] = {
        "split": _split,
        "ocr": _read_words,
        "sort": _sort,
        "duplicates": _duplicates,
        "missing": _missing_step,
    }
    starts = {"split": 0, "ocr": 30, "sort": 40, "duplicates": 55, "missing": 70}
    for step in STEPS:
        await ctx.progress(starts[step], step=step)
        await stages[step](ctx, services)
        await ctx.step(step, _noop)
    await _embed(ctx, services)
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
