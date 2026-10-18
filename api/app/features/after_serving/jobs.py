"""Slice G's worker jobs and its daily reminder run.

- `extract_written_questions`: OCR the round's questions document, identifiers out (rule 7), T-20a finds each
  question as exact words (rule 2: anything reworded is dropped), then T-20b suggests a classification from
  the pack and linked paragraphs. Suggestions only; the expert decides.
- `find_may_matter`: T-21a on the facts of pages added since serving and the confirmed questions. Shown only
  once the page is read (the route enforces the blind read, rule 3).
- `verify_opposing`: T-21b finds the other expert's claims as exact words of their report; code checks each
  against the cited page first (rule 2); only unsettled claims go to T-21c on the larger model; T-21d links
  paragraphs by id only.
- `find_dependents`: T-21e on the other served paragraphs of a changed one.
- `build_supplementary`: the draft supplementary report (only the changes) as a Word file.
- `after_serving_deadline_reminders`: three days before each open deadline, the named expert is told with the
  deadline kind only (rule 6).

Every job finds its work from the table state (jobs carry no payload) and is safe to run again. No text
reaches a log or a job row (rule 6); only exception types are logged.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import date, timedelta
from typing import Any

from app.core.ai.kit import InvalidOutput
from app.core.contracts.case_names import party_names
from app.core.contracts.questions import confirmed_questions
from app.core.jobs import JobContext, registry
from app.core.notifications import notify
from app.core.packs import MergedPack
from app.features.after_serving import ai_tasks, docs, supplementary
from app.features.after_serving.claims import code_check
from app.features.after_serving.common import (
    CLAIM,
    CLAIM_VALUE,
    QUESTION,
    fetch_all,
    fetch_one,
    pack_for,
    page_words,
    paragraphs,
    report_file,
    seal,
    served_version,
    unseal,
)
from app.features.after_serving.services import current
from app.features.after_serving.text import Letter, checked_items
from app.features.after_serving.words import word

logger = logging.getLogger("app.after_serving")

WRITTEN_QUESTIONS = "written_questions"
REMIND_DAYS = 3


async def _run(
    conn: Any, task: Any, *, pack: MergedPack, text: str, case_id: uuid.UUID, names: Sequence[str] = ()
) -> dict[str, Any]:
    """Run an AI task; output that never matches its schema counts as nothing found."""
    try:
        return await current().model().run(conn, task, pack=pack, text=text, case_id=case_id, names=names)
    except InvalidOutput:
        logger.warning("task %s gave no usable output", task.task_id)
        return {}


async def _read_document(ctx: JobContext, pack: MergedPack, document_id: uuid.UUID) -> tuple[Letter, list[str]]:
    services = current()
    storage, crypto = services.files()
    ocr = services.reader()
    async with ctx.transaction() as conn:
        doc = await fetch_one(conn, "select storage_path from app.documents where id = %s", (document_id,))
        assert doc is not None
        parts = []
        for path in sorted(await storage.list(doc["storage_path"])):
            parts.append(await crypto.decrypt(conn, ctx.case_id, await storage.get(path), purpose=path))
        names = await party_names(conn, crypto, ctx.case_id)
    letter = Letter.from_ocr(await ocr.analyse(b"".join(parts), "layout"))
    letter.mask(pack=pack, names=names, dates_of_birth=())
    return letter, names


async def _pack(ctx: JobContext) -> MergedPack:
    async with ctx.transaction() as conn:
        return await pack_for(conn, current().packs, ctx.case_id)


def _ids(values: Any) -> list[str]:
    return [str(v) for v in values] if isinstance(values, list) else []


# --- written questions ----------------------------------------------------------------------------------


@registry.kind("extract_written_questions", interactive=True, changes_case=False)
async def extract_written_questions(ctx: JobContext) -> None:
    pack = await _pack(ctx)
    async with ctx.transaction() as conn:
        rounds = await fetch_all(
            conn,
            "select id, document_id from app.written_question_rounds where case_id = %s and extracted_at is null"
            " and document_id is not null order by number",
            (ctx.case_id,),
        )
    rule_ref = next((str(r.get("rule_ref")) for r in pack.deadline_rules() if r.get("key") == WRITTEN_QUESTIONS), None)
    for rnd in rounds:
        await _extract_round(ctx, pack, rnd["id"], rnd["document_id"], rule_ref)


async def _extract_round(
    ctx: JobContext, pack: MergedPack, round_id: uuid.UUID, document_id: uuid.UUID, rule_ref: str | None
) -> None:
    crypto = current().case_crypto()
    async with ctx.transaction() as conn:
        have = await fetch_one(conn, "select count(*) as n from app.written_questions where round_id = %s", (round_id,))
    if not (have and have["n"]):
        letter, names = await _read_document(ctx, pack, document_id)
        async with ctx.transaction() as conn:
            out = await _run(
                conn, ai_tasks.t20a(pack), pack=pack, text=letter.prompt_text(), case_id=ctx.case_id, names=names
            )
            accepted, rejected = checked_items(letter, out.get("questions", []))
            if rejected:
                logger.info("written questions: %d reworded items dropped", len(rejected))
            seen: set[tuple[str, ...]] = set()
            number = 0
            for _, found in accepted:
                key = tuple(found.word_ids)
                if key in seen:
                    continue
                seen.add(key)
                number += 1
                await conn.execute(
                    "insert into app.written_questions (case_id, round_id, number, para_ref, rule_ref, text_verbatim,"
                    " word_ids) values (%s, %s, %s, %s, %s, %s, %s)",
                    (
                        ctx.case_id,
                        round_id,
                        number,
                        letter.para_ref(found.start),
                        rule_ref,
                        await seal(conn, crypto, ctx.case_id, found.text, QUESTION),
                        found.word_ids,
                    ),
                )
    await _suggest(ctx, pack, round_id)
    async with ctx.transaction() as conn:
        await conn.execute("update app.written_question_rounds set extracted_at = now() where id = %s", (round_id,))


async def _suggest(ctx: JobContext, pack: MergedPack, round_id: uuid.UUID) -> None:
    crypto = current().case_crypto()
    async with ctx.transaction() as conn:
        rows = await fetch_all(
            conn,
            "select id, text_verbatim from app.written_questions where round_id = %s and suggested_classification"
            " is null"
            " order by number",
            (round_id,),
        )
        if not rows:
            return
        served = await served_version(conn, ctx.case_id)
        own = await paragraphs(conn, crypto, ctx.case_id, served) if served else []
        lines = [
            f"[question {r['id']}] {await unseal(conn, crypto, ctx.case_id, r['text_verbatim'], QUESTION)}"
            for r in rows
        ]
        lines += [f"[paragraph {p.id}] {p.text}" for p in own]
        names = await party_names(conn, crypto, ctx.case_id)
        out = await _run(conn, ai_tasks.t20b(pack), pack=pack, text="\n".join(lines), case_id=ctx.case_id, names=names)
        asked = {str(r["id"]) for r in rows}
        known = {str(p.id) for p in own}
        kinds = set(pack.written_question_classifications())
        for item in out.get("items", []):
            qid = str(item.get("question_id"))
            if qid not in asked or item.get("kind") not in kinds:
                continue
            linked = [uuid.UUID(p) for p in dict.fromkeys(_ids(item.get("paragraph_ids"))) if p in known]
            await conn.execute(
                "update app.written_questions set suggested_classification = %s, classification_reason = %s,"
                " suggested_paragraph_ids = %s where id = %s and suggested_classification is null",
                (item["kind"], str(item.get("reason", ""))[:80] or None, linked, uuid.UUID(qid)),
            )
            asked.discard(qid)


# --- new records since serving --------------------------------------------------------------------------


@registry.kind("find_may_matter", interactive=False, changes_case=False)
async def find_may_matter(ctx: JobContext) -> None:
    pack = await _pack(ctx)
    crypto = current().case_crypto()
    async with ctx.transaction() as conn:
        cur = await conn.execute("select app.after_serving_served_at(%s)", (ctx.case_id,))
        row = await cur.fetchone()
        served_at = row[0] if row else None
        if served_at is None:
            return
        facts = await fetch_all(
            conn,
            "select f.id, f.page_id, f.value from app.facts f join app.pages p on p.id = f.page_id"
            " where f.case_id = %s and p.created_at > %s order by p.bundle_page_no, f.id",
            (ctx.case_id, served_at),
        )
        questions = await confirmed_questions(conn, ctx.case_id)
        if not facts or not questions:
            return
        lines = [f"[fact {f['id']}] {f['value']}" for f in facts]
        lines += [f"[question {q.id}] {q.wording}" for q in questions]
        names = await party_names(conn, crypto, ctx.case_id)
        out = await _run(conn, ai_tasks.t21a(pack), pack=pack, text="\n".join(lines), case_id=ctx.case_id, names=names)
        offered = {str(f["id"]): f for f in facts}
        asked = {str(q.id) for q in questions}
        for item in out.get("items", []):
            fact = offered.get(str(item.get("fact_id")))
            qid = str(item.get("question_id"))
            why = str(item.get("why", ""))[:80]
            if fact is None or qid not in asked or not why:
                continue
            await conn.execute(
                "insert into app.may_matter (case_id, page_id, fact_id, question_id, why, job_id)"
                " values (%s, %s, %s, %s, %s, %s) on conflict (fact_id, question_id) do nothing",
                (ctx.case_id, fact["page_id"], fact["id"], uuid.UUID(qid), why, ctx.job_id),
            )


# --- the other expert's report --------------------------------------------------------------------------


@registry.kind("verify_opposing", interactive=False, changes_case=False)
async def verify_opposing(ctx: JobContext) -> None:
    pack = await _pack(ctx)
    async with ctx.transaction() as conn:
        reports = await fetch_all(
            conn,
            "select id, document_id from app.opposing_reports where case_id = %s and checked_at is null order by"
            " created_at",
            (ctx.case_id,),
        )
    for report in reports:
        await _verify_report(ctx, pack, report["id"], report["document_id"])


async def _verify_report(ctx: JobContext, pack: MergedPack, report_id: uuid.UUID, document_id: uuid.UUID) -> None:
    crypto = current().case_crypto()
    async with ctx.transaction() as conn:
        have = await fetch_one(
            conn, "select count(*) as n from app.opposing_claims where document_id = %s", (document_id,)
        )
    if not (have and have["n"]):
        letter, names = await _read_document(ctx, pack, document_id)
        async with ctx.transaction() as conn:
            out = await _run(
                conn, ai_tasks.t21b(pack), pack=pack, text=letter.prompt_text(), case_id=ctx.case_id, names=names
            )
            accepted, _ = checked_items(letter, out.get("claims", []))
            number = 0
            seen: set[tuple[str, ...]] = set()
            for item, found in accepted:
                value = str(item.get("cited_value", ""))
                if tuple(found.word_ids) in seen or not _within(value, found.text):
                    continue
                seen.add(tuple(found.word_ids))
                number += 1
                await conn.execute(
                    "insert into app.opposing_claims (case_id, document_id, claim_no, paragraph_ref, text_verbatim,"
                    " word_ids, cited_page_ref, cited_value) values (%s, %s, %s, %s, %s, %s, %s, %s)",
                    (
                        ctx.case_id,
                        document_id,
                        number,
                        (str(item["paragraph_ref"])[:20] if item.get("paragraph_ref") else None),
                        await seal(conn, crypto, ctx.case_id, found.text, CLAIM),
                        found.word_ids,
                        str(item.get("cited_page", ""))[:20],
                        await seal(conn, crypto, ctx.case_id, value, CLAIM_VALUE),
                    ),
                )
    await _check_claims(ctx, pack, document_id)
    await _compare_positions(ctx, pack, report_id, document_id)


def _within(value: str, text: str) -> bool:
    """The cited value is part of the claim's own words (so it is their words, not the model's)."""
    from app.core.ai import provenance

    want, held = provenance.atoms(value), provenance.atoms(text)
    return bool(want) and any(held[i : i + len(want)] == want for i in range(len(held) - len(want) + 1))


async def _check_claims(ctx: JobContext, pack: MergedPack, document_id: uuid.UUID) -> None:
    crypto = current().case_crypto()
    async with ctx.transaction() as conn:
        claims = await fetch_all(
            conn,
            "select id, text_verbatim, cited_page_ref, cited_value from app.opposing_claims"
            " where document_id = %s and status = 'unchecked' order by claim_no",
            (document_id,),
        )
    for claim in claims:
        async with ctx.transaction() as conn:
            value = await unseal(conn, crypto, ctx.case_id, claim["cited_value"], CLAIM_VALUE) or ""
            ref = str(claim["cited_page_ref"] or "")
            page = (
                await fetch_one(
                    conn, "select id from app.pages where case_id = %s and bundle_page_no = %s", (ctx.case_id, int(ref))
                )
                if ref.isdigit() and len(ref) < 9
                else None
            )
            words = await page_words(conn, crypto, ctx.case_id, page["id"]) if page else None
            settled = code_check(value, words)
            if settled is not None:
                status, evidence, checked_by, reason = settled.status, settled.word_ids, "code", None
            else:
                assert page is not None and words is not None
                text = await unseal(conn, crypto, ctx.case_id, claim["text_verbatim"], CLAIM) or ""
                prompt = "\n".join(
                    [
                        f"[claim] {text}",
                        f"[cited value] {value}",
                        f"[page {ref}]",
                        " ".join(f"{{{w}}}{t}" for w, t in words),
                    ]
                )
                names = await party_names(conn, crypto, ctx.case_id)
                out = await _run(conn, ai_tasks.t21c(pack), pack=pack, text=prompt, case_id=ctx.case_id, names=names)
                if out.get("status") not in ai_tasks.CLAIM_RESULTS:
                    continue  # stays unchecked; the job can run again
                on_page = {w for w, _ in words}
                status, checked_by = str(out["status"]), "model"
                evidence = [w for w in _ids(out.get("evidence_word_ids")) if w in on_page]
                reason = str(out.get("reason", ""))[:80] or None
            await conn.execute(
                "update app.opposing_claims set status = %s, checked_by = %s, evidence_page_id = %s,"
                " evidence_word_ids = %s, reason = %s where id = %s",
                (
                    status,
                    checked_by,
                    page["id"] if page and status != "not_on_page" else None,
                    evidence,
                    reason,
                    claim["id"],
                ),
            )


async def _compare_positions(ctx: JobContext, pack: MergedPack, report_id: uuid.UUID, document_id: uuid.UUID) -> None:
    crypto = current().case_crypto()
    async with ctx.transaction() as conn:
        claims = await fetch_all(
            conn,
            "select paragraph_ref, text_verbatim from app.opposing_claims where document_id = %s order by claim_no",
            (document_id,),
        )
        served = await served_version(conn, ctx.case_id)
        own = await paragraphs(conn, crypto, ctx.case_id, served) if served else []
        questions = await confirmed_questions(conn, ctx.case_id)
        positions: list[dict[str, Any]] = []
        if claims and own and questions:
            lines = [f"[question {q.id}] {q.wording}" for q in questions]
            lines += [f"[own {p.id}] {p.text}" for p in own]
            for c in claims:
                if c["paragraph_ref"]:
                    text = await unseal(conn, crypto, ctx.case_id, c["text_verbatim"], CLAIM)
                    lines.append(f"[theirs {c['paragraph_ref']}] {text}")
            names = await party_names(conn, crypto, ctx.case_id)
            out = await _run(
                conn, ai_tasks.t21d(pack), pack=pack, text="\n".join(lines), case_id=ctx.case_id, names=names
            )
            asked = {str(q.id) for q in questions}
            known_own = {str(p.id) for p in own}
            known_theirs = {str(c["paragraph_ref"]) for c in claims if c["paragraph_ref"]}
            for p in out.get("positions", []):
                if str(p.get("question_id")) not in asked:
                    continue
                mine = [i for i in dict.fromkeys(_ids(p.get("own_paragraph_ids"))) if i in known_own]
                theirs = [i for i in dict.fromkeys(_ids(p.get("their_paragraph_ids"))) if i in known_theirs]
                if mine or theirs:
                    positions.append(
                        {"question_id": str(p["question_id"]), "own_paragraph_ids": mine, "their_paragraph_ids": theirs}
                    )
        import json

        await conn.execute(
            "update app.opposing_reports set checked_at = now(), positions = %s::jsonb,"
            " claims_found = (select count(*) from app.opposing_claims where document_id = %s) where id = %s",
            (json.dumps(positions), document_id, report_id),
        )


# --- supplementary reports ------------------------------------------------------------------------------


@registry.kind("find_dependents", interactive=True, changes_case=False)
async def find_dependents(ctx: JobContext) -> None:
    from app.features.after_serving.common import CHANGE_NEW, CHANGE_OLD

    pack = await _pack(ctx)
    crypto = current().case_crypto()
    async with ctx.transaction() as conn:
        changes = await fetch_all(
            conn,
            "select id, served_version_id, paragraph_id, old_text, new_text, dependent_paragraph_ids"
            " from app.report_changes where case_id = %s and suggested_dependents = '[]'::jsonb"
            " and supplementary_version_id is null order by created_at",
            (ctx.case_id,),
        )
    for change in changes:
        async with ctx.transaction() as conn:
            skip = {change["paragraph_id"], *change["dependent_paragraph_ids"]}
            others = [
                p for p in await paragraphs(conn, crypto, ctx.case_id, change["served_version_id"]) if p.id not in skip
            ]
            if not others:
                continue
            old = await unseal(conn, crypto, ctx.case_id, change["old_text"], CHANGE_OLD)
            new = await unseal(conn, crypto, ctx.case_id, change["new_text"], CHANGE_NEW)
            lines = [f"[changed] {old}", f"[now] {new}", *(f"[paragraph {p.id}] {p.text}" for p in others)]
            names = await party_names(conn, crypto, ctx.case_id)
            out = await _run(
                conn, ai_tasks.t21e(pack), pack=pack, text="\n".join(lines), case_id=ctx.case_id, names=names
            )
            known = {str(p.id) for p in others}
            kept: list[dict[str, Any]] = []
            for item in out.get("items", []):
                pid = str(item.get("paragraph_id"))
                if pid in known and isinstance(item.get("depends"), bool):
                    kept.append(
                        {"paragraph_id": pid, "depends": item["depends"], "reason": str(item.get("reason", ""))[:80]}
                    )
                    known.discard(pid)
            if kept:
                import json

                await conn.execute(
                    "update app.report_changes set suggested_dependents = %s::jsonb where id = %s",
                    (json.dumps(kept), change["id"]),
                )


@registry.kind("build_supplementary", interactive=True, changes_case=True)
async def build_supplementary(ctx: JobContext) -> None:
    pack = await _pack(ctx)
    storage, crypto = current().files()
    async with ctx.transaction() as conn:
        served = await served_version(conn, ctx.case_id)
        if served is None:
            return
        draft = await fetch_one(
            conn,
            "select id, docx_path from app.report_versions where case_id = %s and type = 'supplementary'"
            " and prev_version_id = %s and status = 'draft' order by version_no desc limit 1",
            (ctx.case_id, served),
        )
        if draft is None:
            version_id = uuid.uuid4()
            counts = await fetch_one(
                conn,
                "select coalesce(max(version_no), 0) + 1 as next, count(*) filter (where type = 'supplementary') as n"
                " from app.report_versions where case_id = %s",
                (ctx.case_id,),
            )
            assert counts is not None
            await conn.execute(
                "insert into app.report_versions (id, case_id, version_no, type, display_name, prev_version_id)"
                " values (%s, %s, %s, 'supplementary', %s, %s)",
                (
                    version_id,
                    ctx.case_id,
                    counts["next"],
                    word(pack.language, "supplementary", n=int(counts["n"]) + 1),
                    served,
                ),
            )
        else:
            version_id = draft["id"]
        await conn.execute(
            "update app.report_changes set supplementary_version_id = %s where case_id = %s"
            " and served_version_id = %s and supplementary_version_id is null",
            (version_id, ctx.case_id, served),
        )
        path = report_file(ctx.case_id, version_id, "supplementary.docx")
        data = docs.build(await supplementary.blocks(conn, crypto, ctx.case_id, pack, version_id))
        await storage.put(path, await crypto.encrypt(conn, ctx.case_id, data, purpose=path))
        await conn.execute("update app.report_versions set docx_path = %s where id = %s", (path, version_id))


# --- reminders ------------------------------------------------------------------------------------------


@registry.schedule("after_serving_deadline_reminders")
async def deadline_reminders(conn: Any, run_date: date) -> None:
    """Three days before an open deadline, tell the named expert (the deadline kind only, rule 6)."""
    rows = await fetch_all(conn, "select * from app.after_serving_due_deadlines(%s, %s)", (run_date, REMIND_DAYS))
    for row in rows:
        due: date = row["due_on"]
        first_day = due - timedelta(days=REMIND_DAYS)
        if run_date < first_day or (row["last_reminded_on"] is not None and row["last_reminded_on"] >= first_day):
            continue
        if row["named_expert_id"] is None:
            continue
        async with conn.transaction():
            await conn.execute("select set_config('app.job_case_id', %s, true)", (str(row["case_id"]),))
            await notify(
                conn,
                current().mailer,
                person_id=row["named_expert_id"],
                organisation_id=row["organisation_id"],
                kind=f"deadline.{row['kind']}",
                case_id=row["case_id"],
            )
            await conn.execute(
                "update app.deadlines set last_reminded_on = %s where id = %s", (run_date, row["deadline_id"])
            )
            await conn.execute("select set_config('app.job_case_id', '', true)")
