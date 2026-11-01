"""Slice I's jobs. Each finds its rows by its own job id and is safe to run again (one step each).

- `read_agenda`: the agenda's words (text layer, else OCR Read), T-22a, then T-22b for new items. A new
  version moves a statement out for signature back to draft.
- `match_trial_bundle`: the court bundle's pages (text layer, else OCR Read) matched to the expert's
  numbered pages by what is on them (`matching.py`); one concordance row per page.
- `build_trial_pack`: the large-print trial pack (`pack_document.py`), hashed and stored.
- `reseal_trial_files`: one-off, seals trial files stored before sealing (`reseal.py`).

Stored files are sealed under the case key (`storage.py`); hashes are over the plain file.
"""

from __future__ import annotations

import json
import uuid

from psycopg import AsyncConnection

from app.core.contracts.case_identity import identity_for
from app.core.contracts.document_words import apply_corrections, current_corrections, word_purpose
from app.core.contracts.served import latest_served
from app.core.jobs import JobContext, registry
from app.core.pdf import embedded_words, split_pages
from app.features.trial import agenda as agenda_reading
from app.features.trial import ai_tasks, freshness, repo
from app.features.trial.common import pack_for, sha256, trial_path
from app.features.trial.matching import OurPage, match_pages
from app.features.trial.pack_document import pack_document
from app.features.trial.pdfs import render_counted
from app.features.trial.reseal import reseal_plain_files
from app.features.trial.services import TrialServices, current
from app.features.trial.storage import get_sealed, put_sealed


async def page_texts(services: TrialServices, pdf: bytes) -> list[list[str]]:
    """The words of each page: the text layer, or OCR Read for pages without one."""
    pages = [[w.text for w in embedded_words(page).words] for page in split_pages(pdf)]
    if any(not words for words in pages) and services.ocr is not None:
        result = await services.ocr.analyse(pdf, "read")
        for n, page in enumerate(result.pages):
            if n < len(pages) and not pages[n]:
                pages[n] = [w.text for w in page.words]
    return pages


@registry.kind("read_agenda", interactive=True)
async def read_agenda(ctx: JobContext) -> None:
    services = current()

    async def work(conn: AsyncConnection) -> None:
        version = await repo.one(conn, "select * from app.agenda_versions where job_id = %s", (ctx.job_id,))
        if version is None:
            return
        crypto, kit = services.case_crypto(), services.model()
        pack = await pack_for(conn, services.packs, ctx.case_id)
        pdf = await get_sealed(services, conn, ctx.case_id, version["storage_path"])
        flat = [w for page in await page_texts(services, pdf) for w in page]
        words = {f"w{n}": text for n, text in enumerate(flat, start=1)}
        request = "\n".join(f"{ref}\t{text}" for ref, text in words.items())
        who = await identity_for(conn, crypto, ctx.case_id)
        answer = await kit.run(
            conn,
            ai_tasks.t22a(pack),
            pack=pack,
            text=request,
            case_id=ctx.case_id,
            subject="agenda",
            names=who.names,
            dates_of_birth=who.dates_of_birth,
        )
        copied = agenda_reading.copied_items(answer, words)
        earlier = await repo.one(
            conn,
            "select id from app.agenda_versions where meeting_id = %s and version_no < %s and read_at is not null"
            " order by version_no desc limit 1",
            (version["meeting_id"], version["version_no"]),
        )
        previous = await repo.items(conn, crypto, ctx.case_id, earlier["id"]) if earlier else None
        served = await latest_served(conn, crypto, ctx.case_id)
        await agenda_reading.insert_items(
            conn, crypto, kit, pack, ctx.case_id, version["meeting_id"], version["id"], copied, previous, served
        )
        await conn.execute(
            "update app.agenda_versions set read_at = now(), item_count = %s where id = %s",
            (len(copied), version["id"]),
        )
        await conn.execute(
            "update app.expert_meetings set status = 'draft', signing_reference_sha256 = null, sent_at = null,"
            " prepared_path = null, prepared_sha256 = null, prepared_pages = null, prepared_at = null"
            " where id = %s and status in ('draft', 'out_for_signature')",
            (version["meeting_id"],),
        )

    await ctx.step("read", work)


async def our_pages(services: TrialServices, conn: AsyncConnection, case_id: uuid.UUID) -> list[OurPage]:
    crypto = services.case_crypto()
    cur = await conn.execute(
        "select p.id, p.bundle_page_no, w.word_id, w.text from app.pages p"
        " left join app.ocr_words w on w.page_id = p.id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s)"
        " and p.bundle_page_no is not null order by p.bundle_page_no, w.n",
        (case_id,),
    )
    texts: dict[uuid.UUID, tuple[int, dict[str, str]]] = {}
    for page_id, page_no, word_id, sealed in await cur.fetchall():
        entry = texts.setdefault(page_id, (int(page_no), {}))
        if sealed is not None:
            opened = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page_id, word_id))
            entry[1][word_id] = opened.decode()
    corrections = await current_corrections(conn, crypto, case_id, texts)  # the expert's reading (slice B, 0018)
    return [
        OurPage(pid, no, " ".join(t for t in apply_corrections(words, corrections.get(pid, [])).values() if t))
        for pid, (no, words) in texts.items()
    ]


@registry.kind("match_trial_bundle", interactive=True)
async def match_trial_bundle(ctx: JobContext) -> None:
    services = current()

    async def work(conn: AsyncConnection) -> None:
        bundle = await repo.one(conn, "select * from app.court_bundles where job_id = %s", (ctx.job_id,))
        if bundle is None or bundle["status"] == "superseded":
            return
        pdf = await get_sealed(services, conn, ctx.case_id, bundle["storage_path"])
        theirs = [" ".join(words) for words in await page_texts(services, pdf)]
        ours = await our_pages(services, conn, ctx.case_id)
        by_key = {p.key: p for p in ours}
        for match in match_pages(ours, theirs):
            page = by_key[match.key]
            await conn.execute(
                "insert into app.concordance (case_id, court_bundle_id, page_id, page_no, trial_page_no, score,"
                " method, confirmed) values (%s, %s, %s, %s, %s, %s, %s, %s)"
                " on conflict (court_bundle_id, page_id) do nothing",
                (
                    ctx.case_id,
                    bundle["id"],
                    match.key,
                    page.page_no,
                    match.trial_page_no,
                    match.score,
                    match.method,
                    match.confirmed,
                ),
            )
        await conn.execute(
            "update app.court_bundles set status = 'matched', page_count = %s where id = %s",
            (len(theirs), bundle["id"]),
        )

    await ctx.step("match", work)


@registry.kind("build_trial_pack", interactive=True, changes_case=False)
async def build_trial_pack(ctx: JobContext) -> None:
    services = current()

    async def work(conn: AsyncConnection) -> None:
        settings = await repo.one(conn, "select * from app.trial_pack_settings where case_id = %s", (ctx.case_id,))
        pack = await pack_for(conn, services.packs, ctx.case_id)
        built = await pack_document(conn, services, pack, ctx.case_id, settings)
        if built is None:
            return
        pdf, pages, per_section = render_counted(built.document)
        pack_id = uuid.uuid4()
        path = trial_path(ctx.case_id, "packs", f"{pack_id}.pdf")
        await put_sealed(services, conn, ctx.case_id, path, pdf)
        hearing_id = settings["hearing_id"] if settings else None
        built_from = await freshness.stamp(conn, ctx.case_id, hearing_id)
        await conn.execute(
            "insert into app.trial_packs (id, case_id, hearing_id, job_id, report_version_id, court_bundle_id,"
            " sections, include_guidance, sha256, storage_path, page_count, pack_versions, section_pages,"
            " built_hearing_version, built_last_page_no, built_served_version_id)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                pack_id,
                ctx.case_id,
                hearing_id,
                ctx.job_id,
                built.report_version_id,
                built.court_bundle_id,
                built.sections,
                built.include_guidance,
                sha256(pdf),
                path,
                pages,
                json.dumps(pack.versions),
                json.dumps([{"key": k, "pages": n} for k, n in per_section]),
                *built_from.values(),
            ),
        )

    await ctx.step("build", work)


@registry.kind("reseal_trial_files", interactive=False, changes_case=False)
async def reseal_trial_files(ctx: JobContext) -> None:
    """One-off: seal this case's trial files written before sealing (safe to run again)."""
    services = current()

    async def work(conn: AsyncConnection) -> None:
        await reseal_plain_files(conn, services, ctx.case_id)

    await ctx.step("reseal", work)
