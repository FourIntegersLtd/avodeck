"""Slice E's worker jobs: `suggest_points` (T-14a, T-14b) and `compare_witness_statement` (T-14c).

`suggest_points` goes through the case's answerable questions. For each, it sends the question and the
citable facts linked to it that are not yet points (slice B's read log decides what is citable, fail
closed), each under a short reference, through the AI kit: identifiers out first (rule 7), the pack's
sides in the context, an output schema that allows only the references supplied. An answer naming any
other fact fails the schema and is asked once more; a second wrong answer adds nothing for that
question. Kept answers are stored as suggested points with their short reason; a fact already a point
is never sent again, so a point the expert has decided is never changed. No text reaches a log or a
job row (rule 6).

For a question with a saved draft, it then sends the draft and the linked citable facts the draft does
not cite and that were not suggested before (T-14b, same checks); kept answers are stored as relevant
facts with a short label, for the expert to use or dismiss on screen 14.

`compare_witness_statement` takes each comparison queued under its job. A statement already compared
adds nothing. Otherwise it sends the statement's words and the case's citable facts (same checks). Each
claim returned must be an exact run of the statement's words: code finds that run and keeps those words
with their references; a claim not in the statement is dropped. A claim with no verdict is kept as
not_addressed. Two wrong answers keep nothing and the comparison is marked refused.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from psycopg import AsyncConnection

from app.core.ai import provenance
from app.core.ai.kit import AiKit, InvalidOutput
from app.core.contracts.facts import Fact, citable_facts
from app.core.jobs import JobContext, registry
from app.core.packs import MergedPack
from app.features.write import ai_tasks
from app.features.write.common import ANSWERABLE
from app.features.write.routes_opinion import BODY
from app.features.write.routes_witness import claim_purpose
from app.features.write.services import WriteServices, current

logger = logging.getLogger("app.write")


async def _case_pack(conn: AsyncConnection, services: WriteServices, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    assert row is not None
    return services.packs.combine(str(row[0]), str(row[1]), str(row[2]))


def _fact_line(ref: str, fact: Fact) -> str:
    when = fact.event_date.isoformat() if fact.event_date else ""
    return "\t".join((ref, fact.kind, when, " ".join((fact.value or "").split())))


@registry.kind("suggest_points", interactive=True, changes_case=False)
async def suggest_points(ctx: JobContext) -> None:
    services = current()
    kit = services.ai()
    async with ctx.transaction() as conn:
        pack = await _case_pack(conn, services, ctx.case_id)
        cur = await conn.execute(
            "select id, coalesce(confirmed_text, text_verbatim) from app.questions"
            " where case_id = %s and status = any(%s) order by number",
            (ctx.case_id, list(ANSWERABLE)),
        )
        questions = [(r[0], str(r[1])) for r in await cur.fetchall()]
    for number, (question_id, text) in enumerate(questions):
        await ctx.progress(int(100 * number / len(questions)), step="points")
        async with ctx.transaction() as conn:
            await _sort(ctx, conn, services, kit, pack, question_id, text)
        async with ctx.transaction() as conn:
            await _relevant(ctx, conn, services, kit, pack, question_id)


async def _sort(
    ctx: JobContext,
    conn: AsyncConnection,
    services: WriteServices,
    kit: AiKit,
    pack: MergedPack,
    question_id: uuid.UUID,
    question: str,
) -> None:
    linked = await services.links.linked(
        conn, ctx.case_id, question_id, await citable_facts(conn, ctx.case_id, services.reads)
    )
    cur = await conn.execute("select fact_id from app.points where question_id = %s", (question_id,))
    used = {r[0] for r in await cur.fetchall()}
    refs = {f"f{i}": f for i, f in enumerate((f for f in linked if f.id not in used), 1)}
    if not refs:
        return
    text = "\n".join([f"question\t{' '.join(question.split())}", *(_fact_line(r, f) for r, f in refs.items())])
    task = ai_tasks.t14a(pack, list(refs))
    names = await services.party_names().case_names(conn, ctx.case_id)
    try:
        out: dict[str, Any] = await kit.run(
            conn, task, pack=pack, text=text, case_id=ctx.case_id, names=names, subject=f"question:{question_id}"
        )
    except InvalidOutput:
        logger.warning("T-14a answer refused twice for one question; no points added")
        return
    seen: set[str] = set()
    for item in out["points"]:
        if item["fact"] in seen:
            continue
        seen.add(item["fact"])
        fact = refs[item["fact"]]
        await conn.execute(
            "insert into app.points (case_id, question_id, fact_id, side, origin, reason, fact_version, job_id,"
            " model_task, prompt_version) values (%s, %s, %s, %s, 'ai', %s, %s, %s, %s, %s)"
            " on conflict (question_id, fact_id) do nothing",
            (
                ctx.case_id,
                question_id,
                fact.id,
                item["side"],
                item["reason"],
                fact.version,
                ctx.job_id,
                task.task_id,
                task.prompt_version,
            ),
        )


async def _relevant(
    ctx: JobContext,
    conn: AsyncConnection,
    services: WriteServices,
    kit: AiKit,
    pack: MergedPack,
    question_id: uuid.UUID,
) -> None:
    cur = await conn.execute("select body, citation_refs from app.opinions where question_id = %s", (question_id,))
    opinion = await cur.fetchone()
    if opinion is None:
        return
    cited = {str(c["fact_id"]) for c in opinion[1]}
    cur = await conn.execute("select fact_id from app.relevant_facts where question_id = %s", (question_id,))
    asked = {r[0] for r in await cur.fetchall()}
    linked = await services.links.linked(
        conn, ctx.case_id, question_id, await citable_facts(conn, ctx.case_id, services.reads)
    )
    spare = (f for f in linked if str(f.id) not in cited and f.id not in asked)
    refs = {f"f{i}": f for i, f in enumerate(spare, 1)}
    if not refs:
        return
    body = (await services.case_crypto().decrypt(conn, ctx.case_id, bytes(opinion[0]), purpose=BODY)).decode()
    text = "\n".join([f"draft\t{' '.join(body.split())}", *(_fact_line(r, f) for r, f in refs.items())])
    task = ai_tasks.t14b(pack, list(refs))
    names = await services.party_names().case_names(conn, ctx.case_id)
    try:
        out: dict[str, Any] = await kit.run(
            conn, task, pack=pack, text=text, case_id=ctx.case_id, names=names, subject=f"draft:{question_id}"
        )
    except InvalidOutput:
        logger.warning("T-14b answer refused twice for one question; nothing suggested")
        return
    for item in out["facts"]:
        fact = refs[item["fact"]]
        await conn.execute(
            "insert into app.relevant_facts (case_id, question_id, fact_id, why_relevant, fact_version, job_id,"
            " model_task, prompt_version) values (%s, %s, %s, %s, %s, %s, %s, %s)"
            " on conflict (question_id, fact_id) do nothing",
            (
                ctx.case_id,
                question_id,
                fact.id,
                item["why_relevant"],
                fact.version,
                ctx.job_id,
                task.task_id,
                task.prompt_version,
            ),
        )


@registry.kind("compare_witness_statement", interactive=True, changes_case=False)
async def compare_witness_statement(ctx: JobContext) -> None:
    services = current()
    kit = services.ai()
    async with ctx.transaction() as conn:
        cur = await conn.execute(
            "select id, document_id from app.witness_comparisons where job_id = %s and status = 'queued'"
            " order by created_at, id",
            (ctx.job_id,),
        )
        asked = await cur.fetchall()
    for comparison_id, document_id in asked:
        async with ctx.transaction() as conn:
            await _compare(ctx, conn, services, kit, comparison_id, document_id)


def locate(quote: str, words: list[tuple[str, str]]) -> list[tuple[str, str]] | None:
    """The run of `words` whose text is exactly `quote` (case, spacing and number format aside), or None."""
    target = provenance.atoms(quote)
    each = [provenance.atoms(text) for _, text in words]
    for start in range(len(words)):
        if not target or not each[start] or each[start][0] != target[0]:
            continue
        run: list[str] = []
        for end in range(start, len(words)):
            run += each[end]
            if run == target:
                return words[start : end + 1]
            if run != target[: len(run)]:
                break
    return None


async def _finish(conn: AsyncConnection, comparison_id: uuid.UUID, status: str, claims: int | None) -> None:
    await conn.execute(
        "update app.witness_comparisons set status = %s, claims = %s where id = %s", (status, claims, comparison_id)
    )


async def _compare(
    ctx: JobContext,
    conn: AsyncConnection,
    services: WriteServices,
    kit: AiKit,
    comparison_id: uuid.UUID,
    document_id: uuid.UUID,
) -> None:
    cur = await conn.execute("select count(*) from app.witness_claims where document_id = %s", (document_id,))
    row = await cur.fetchone()
    if row and row[0]:
        await _finish(conn, comparison_id, "done", row[0])
        return
    words = await services.statement_words().words(conn, ctx.case_id, document_id)
    refs = {f"f{i}": f for i, f in enumerate(await citable_facts(conn, ctx.case_id, services.reads), 1)}
    if not words or not refs:
        await _finish(conn, comparison_id, "done", 0)
        return
    pack = await _case_pack(conn, services, ctx.case_id)
    statement = " ".join(" ".join(text for _, text in words).split())
    text = "\n".join([f"statement\t{statement}", *(_fact_line(r, f) for r, f in refs.items())])
    task = ai_tasks.t14c(pack, list(refs))
    names = await services.party_names().case_names(conn, ctx.case_id)
    try:
        out: dict[str, Any] = await kit.run(
            conn, task, pack=pack, text=text, case_id=ctx.case_id, names=names, subject=f"statement:{document_id}"
        )
    except InvalidOutput:
        logger.warning("T-14c answer refused twice for one statement; no claims added")
        await _finish(conn, comparison_id, "refused", None)
        return
    verdicts: dict[int, dict[str, Any]] = {}
    for verdict in out["verdicts"]:
        verdicts.setdefault(verdict["claim_index"], verdict)
    crypto = services.case_crypto()
    kept = 0
    for index, claim in enumerate(out["claims"]):
        found = locate(claim["text_verbatim"], words)
        if found is None:
            continue
        verdict = verdicts.get(index, {"fact_ids": [], "verdict": "not_addressed"})
        facts = [refs[r] for r in dict.fromkeys(verdict["fact_ids"])]
        words_text = " ".join(text for _, text in found)
        sealed = await crypto.encrypt(conn, ctx.case_id, words_text.encode(), purpose=claim_purpose(document_id, index))
        await conn.execute(
            "insert into app.witness_claims (case_id, document_id, claim_index, text_verbatim, word_ids, verdict,"
            " fact_ids, fact_versions, job_id, model_task, prompt_version)"
            " values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                ctx.case_id,
                document_id,
                index,
                sealed,
                [ref for ref, _ in found],
                verdict["verdict"],
                [f.id for f in facts],
                [f.version for f in facts],
                ctx.job_id,
                task.task_id,
                task.prompt_version,
            ),
        )
        kept += 1
    await _finish(conn, comparison_id, "done", kept)
