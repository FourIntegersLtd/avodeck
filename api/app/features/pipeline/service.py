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

Every step checks what already exists, so a retried or repeated job changes nothing. Job rows and
logs carry numbers and step codes only (rule 6).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from typing import Any

from psycopg import AsyncConnection

from app.core import audit
from app.core.ai import pii
from app.core.ai.answers import page_subject, reference_subject
from app.core.ai.cell import model_for
from app.core.ai.kit import InvalidOutput
from app.core.ai.ocr import OcrPage
from app.core.contracts.pipeline import PROCESS_BUNDLE, PROCESS_PAGES
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext
from app.core.pdf import split_pages
from app.features.pipeline import dates, duplicates, extract, gaps, missing, paths, repo
from app.features.pipeline.render import render
from app.features.pipeline.wiring import PipelineServices, current
from app.features.pipeline.words import assess, usable_text_layer

SCAN_DOCUMENT = "scan_document"
CONCURRENCY = 3  # pages read or sent to a model at once
STEPS = ("split", "ocr", "extract", "sort", "duplicates", "gaps", "missing")


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


async def bounded[T](items: Sequence[T], work: Callable[[T], Awaitable[None]], limit: int = CONCURRENCY) -> None:
    """Run `work` on every item, at most `limit` at once; the first error stops the rest."""
    gate = asyncio.Semaphore(limit)

    async def one(item: T) -> None:
        async with gate:
            await work(item)

    tasks = [asyncio.create_task(one(item)) for item in items]
    try:
        await asyncio.gather(*tasks)
    except BaseException:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _page_pdf(conn: AsyncConnection, services: PipelineServices, case_id: uuid.UUID, page_id: uuid.UUID) -> bytes:
    path = paths.page_pdf_path(case_id, page_id)
    return await services.crypto.decrypt(conn, case_id, await services.storage.get(path), purpose=path)


async def _read_words(ctx: JobContext, services: PipelineServices) -> None:
    async with ctx.transaction() as conn:
        waiting = await repo.pages_without_words(conn, ctx.case_id)

    async def read(page_id: uuid.UUID) -> None:
        async with ctx.transaction() as conn:
            page_pdf = await _page_pdf(conn, services, ctx.case_id, page_id)
        page: OcrPage | None = usable_text_layer(page_pdf)
        source = "embedded"
        if page is None:
            if services.ocr is None:
                raise ProcessingPaused("model_unavailable")
            page = (await services.ocr.analyse(page_pdf, "layout")).pages[0]
            source = "ocr"
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
                handwriting=reading.handwriting,
                unreadable=reading.unreadable,
                size=(page.width, page.height),
            )

    await bounded(waiting, read)


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


async def _extract(ctx: JobContext, services: PipelineServices) -> None:
    async with ctx.transaction() as conn:
        waiting = await repo.pages_to_extract(conn, ctx.case_id)
        if not waiting:
            return
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
        ordered = [(w.word_id, texts[w.word_id]) for w in stored]
        struck = {w.word_id for w in stored if w.struck}
        stripped = pii.strip(extract.plain_text(ordered), pack=pack, names=who.names, dates_of_birth=who.dates_of_birth)
        text = extract.page_text(ordered, stripped.text)
        subject = page_subject(ordered)
        output: dict[str, Any] | None = None
        kept: list[Any] = []
        reason: str | None = None
        for _ in range(2):
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
            )
        done += 1
        if done % 25 == 0:
            await ctx.progress(min(90, 40 + 50 * done // len(waiting)), step="extract")

    await bounded(waiting, one)


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

    await bounded(unhashed, hash_one)
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
        await repo.store_gaps(conn, ctx.case_id, gaps.find(await repo.gap_pages(conn, ctx.case_id)))


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

    await bounded(waiting, one)


STAGES: dict[str, Callable[[JobContext, PipelineServices], Awaitable[None]]] = {
    "extract": _extract,
    "duplicates": _duplicates,
    "gaps": _gaps,
    "missing": _missing,
}


async def process_bundle(ctx: JobContext) -> None:
    services = current()
    await ctx.progress(0, step="split")
    await _split(ctx, services)
    await ctx.progress(30, step="ocr")
    await _read_words(ctx, services)
    for n, step in enumerate(STEPS, start=1):
        if step in STAGES:
            await STAGES[step](ctx, services)
        await ctx.step(step, _noop)
        await ctx.progress(100 * n // len(STEPS), step=step)


async def queue_pages(conn: AsyncConnection, case_id: uuid.UUID, page_ids: object) -> uuid.UUID:
    """The `process_pages` entry point other slices call (core/contracts/pipeline.py)."""
    from app.features.pipeline.wiring import registry

    return await registry().enqueue(conn, case_id, PROCESS_PAGES)
