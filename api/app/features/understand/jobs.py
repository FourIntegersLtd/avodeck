"""Slice D's background jobs: `suggest_links` (T-11) and `rerun_checks` (checks and T-13a).

Both are queued by the routes and run in the worker under the job's case (ADR 0013).

`rerun_checks` first works out when each fact reached the clinician, in code (`availability.py`), and
writes it with `app.set_fact_availability` (no new fact version). An entry that arrives with no mark is
unknown, never the date of the letter itself.

It then runs the checks the case's named expert has switched on (`check_config`, the pack's defaults for the
rest) on checked facts only (rule 2). A check that needs the words labelled (`label_spans`) asks T-13a
about the cited words of the entries that could be the action; code keeps a label only if its words lie
inside that entry's own words, and the rule decides, never the model (rule 1). Each run writes one
`check_runs` row. A finding that is found again stays the same row, so the expert's decision stays with it
(the database refuses a finding that starts decided); one that is no longer found becomes `gone`, never
deleted and never closed otherwise. A switched-off check leaves its earlier findings as they are.

No text reaches a log or a job row (rule 6); identifiers are stripped before T-13a (rule 7).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core.ai import pii
from app.core.contracts import availability as evidence
from app.core.contracts import questions
from app.core.jobs import JobContext, registry
from app.core.packs import MergedPack
from app.features.understand import ai_tasks, availability, guidelines, links
from app.features.understand.checks.engine import configured_checks
from app.features.understand.checks.rules import CheckFact, Hit, evaluate, to_label
from app.features.understand.repo import CHECKED
from app.features.understand.services import current

TOP_K = 10  # the facts closest to each question that are offered to T-11
PARTY_NAME = "party_name"  # the purpose slice C seals a party's name under


async def _case_pack(conn: AsyncConnection, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    assert row is not None
    return current().packs.combine(str(row[0]), str(row[1]), str(row[2]))


def _stamp(day: date | None) -> datetime | None:
    return None if day is None else datetime(day.year, day.month, day.day, tzinfo=UTC)


async def write_availability(conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack) -> None:
    """Work out and store `available_at` and its basis for every fact that is not rejected."""
    positions = await evidence.fact_positions(conn, case_id)
    own = sorted(pack.own_record_sources())
    cur = await conn.execute(
        "select f.id, f.page_id, f.kind, f.event_date, coalesce(p.source_type = any (%s), false),"
        " f.available_at, f.availability_basis from app.facts f join app.pages p on p.id = f.page_id"
        " where f.case_id = %s and f.status <> 'rejected'",
        (own, case_id),
    )
    rows = await cur.fetchall()
    placed = [availability.Placed(r[0], r[1], positions.get(r[0], 0), r[2], r[3], bool(r[4])) for r in rows]
    marks = [
        availability.Mark(e.page_id, e.kind, e.date, e.position)
        for e in await evidence.evidence_for_case(conn, case_id)
    ]
    cur = await conn.execute(
        "select fact_id, (available_at at time zone 'UTC')::date, unknown, created_at"
        " from app.availability_overrides where case_id = %s",
        (case_id,),
    )
    overrides = [availability.Override(r[0], r[1], bool(r[2]), r[3]) for r in await cur.fetchall()]
    worked = availability.compute(placed, marks, pack.arriving_kinds(), overrides)
    for fact_id, _, _, _, _, stored_at, stored_basis in rows:
        got = worked[fact_id]
        if (_stamp(got.available_at), got.basis) == (stored_at, stored_basis):
            continue
        await conn.execute(
            "select app.set_fact_availability(%s, %s, %s)", (fact_id, _stamp(got.available_at), got.basis)
        )


def word_purpose(page_id: uuid.UUID, word_id: str) -> str:
    """The purpose slice A seals each word of `ocr_words` under."""
    return f"ocr_words/{page_id}/{word_id}"


async def party_names(conn: AsyncConnection, case_id: uuid.UUID) -> list[str]:
    """The case's party names and their parts (words reach the model one per line), for `pii.strip`."""
    crypto = current().words()
    cur = await conn.execute("select name from app.case_parties where case_id = %s order by created_at, id", (case_id,))
    names: list[str] = []
    for (sealed,) in await cur.fetchall():
        full = (await crypto.decrypt(conn, case_id, bytes(sealed), purpose=PARTY_NAME)).decode()
        names += [full, *(part for part in full.split() if len(part) > 2)]
    return names


async def cited_words(
    conn: AsyncConnection, case_id: uuid.UUID, facts: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[str, str]]]:
    """(word id, text) of the words each fact cites, in reading order."""
    crypto = current().words()
    cur = await conn.execute(
        "select f.id, w.page_id, w.word_id, w.text from app.facts f"
        " join app.ocr_words w on w.page_id = f.page_id and w.word_id = any (f.word_ids)"
        " where f.case_id = %s and f.id = any (%s) order by f.id, w.n",
        (case_id, list(facts)),
    )
    out: dict[uuid.UUID, list[tuple[str, str]]] = {}
    for fact_id, page_id, word_id, sealed in await cur.fetchall():
        text = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page_id, word_id))
        out.setdefault(fact_id, []).append((word_id, text.decode()))
    return out


async def _checked_facts(conn: AsyncConnection, case_id: uuid.UUID) -> list[CheckFact]:
    cur = await conn.execute(
        "select f.id, f.page_id, f.kind, f.fields, f.value, f.event_date, (f.available_at at time zone 'UTC')::date,"
        " f.status, exists (select 1 from app.fact_question_links l where l.fact_id = f.id and l.status = 'confirmed')"
        " from app.facts f where f.case_id = %s and f.status = any (%s) order by f.event_date nulls last, f.id",
        (case_id, list(CHECKED)),
    )
    return [CheckFact(r[0], r[1], r[2], r[3], r[4], r[5], r[6], bool(r[8]), r[7]) for r in await cur.fetchall()]


async def _expert_settings(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, dict[str, Any]]:
    cur = await conn.execute(
        "select c.check_key, c.enabled, c.window_days from app.check_config c"
        " join app.cases k on k.named_expert_id = c.person_id where k.id = %s",
        (case_id,),
    )
    return {
        r[0]: {"enabled": r[1], **({"window_days": r[2]} if r[2] is not None else {})} for r in await cur.fetchall()
    }


def _tables(pack: MergedPack, checks: Sequence[Mapping[str, Any]]) -> dict[str, list[Mapping[str, Any]]]:
    """The pack's reference tables the checks name (`table`)."""
    return {name: list(pack.reference_table(name)) for name in {str(c["table"]) for c in checks if c.get("table")}}


async def _labels(
    ctx: JobContext, pack: MergedPack, check: Mapping[str, Any], facts: list[CheckFact]
) -> dict[uuid.UUID, str]:
    """T-13a for the entries a check needs labelled; only labels on the entry's own words are kept."""
    wanted = to_label(check, facts)
    if not wanted:
        return {}
    kit = current().ai()
    async with ctx.transaction() as conn:
        words = {f: w for f, w in (await cited_words(conn, ctx.case_id, wanted)).items() if w}
        if not words:
            return {}
        names = await party_names(conn, ctx.case_id)
        born = await current().dates_of_birth(conn, ctx.case_id)
        text = ai_tasks.t13a_text(str(check["key"]), list(words.items()))
        out = await kit.run(
            conn, ai_tasks.t13a(pack), pack=pack, text=text, case_id=ctx.case_id, names=names, dates_of_birth=born
        )
    return ai_tasks.accept_labels(out, {f: [wid for wid, _ in w] for f, w in words.items()})


async def _save(
    conn: AsyncConnection,
    ctx: JobContext,
    pack: MergedPack,
    ran: Sequence[Mapping[str, Any]],
    hits: Sequence[tuple[Mapping[str, Any], Hit]],
    pages: Mapping[uuid.UUID, uuid.UUID],
) -> None:
    version = "+".join(pack.versions.values())[:80]
    cur = await conn.execute(
        "insert into app.check_runs (case_id, job_id, pack_version, checks) values (%s, %s, %s, %s) returning id",
        (ctx.case_id, ctx.job_id, version, Jsonb([str(c["key"]) for c in ran])),
    )
    row = await cur.fetchone()
    assert row is not None
    run_id = row[0]
    cur = await conn.execute(
        "select id, check_key, evidence_fact_ids, status from app.findings where case_id = %s", (ctx.case_id,)
    )
    old = {(r[1], frozenset(r[2])): (r[0], r[3]) for r in await cur.fetchall()}
    seen: set[tuple[str, frozenset[uuid.UUID]]] = set()
    for check, hit in hits:
        key = (hit.check_key, frozenset(hit.evidence))
        seen.add(key)
        if key in old:
            await conn.execute(
                "update app.findings set status = 'open', run_id = %s, pack_version = %s where id = %s",
                (run_id, version, old[key][0]),
            )
            continue
        evidence_pages = list(dict.fromkeys(pages[f] for f in hit.evidence))
        await conn.execute(
            "insert into app.findings (case_id, check_key, rule, gentle, evidence_fact_ids, evidence_page_ids,"
            " pack_version, run_id) values (%s, %s, %s, %s, %s, %s, %s, %s)"
            " on conflict (case_id, check_key, app.sorted_uuids(evidence_fact_ids))"
            " do update set status = 'open', run_id = excluded.run_id, pack_version = excluded.pack_version",
            (
                ctx.case_id,
                hit.check_key,
                str(check["rule"]),
                hit.gentle,
                list(hit.evidence),
                evidence_pages,
                version,
                run_id,
            ),
        )
    ran_keys = {str(c["key"]) for c in ran}
    for (check_key, evidence_ids), (finding_id, status) in old.items():
        if check_key in ran_keys and status == "open" and (check_key, evidence_ids) not in seen:
            await conn.execute("update app.findings set status = 'gone' where id = %s", (finding_id,))


async def _confirmed_questions(conn: AsyncConnection, case_id: uuid.UUID) -> list[tuple[uuid.UUID, str]]:
    return [(q.id, q.wording) for q in await questions.confirmed_questions(conn, case_id)]


def _day(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


@registry.kind("suggest_links", interactive=False, changes_case=False)
async def suggest_links(ctx: JobContext) -> None:
    """Suggest (never decide) links between checked facts and the confirmed questions (T-11).

    Question and fact wording is identifier-stripped before it is embedded and again inside the AI kit
    (rule 7). Code keeps only pairs it offered; an existing link of any status is left exactly as it is.
    """
    services = current()
    async with ctx.transaction() as conn:
        pack = await _case_pack(conn, ctx.case_id)
        questions = await _confirmed_questions(conn, ctx.case_id)
        facts = [f for f in await _checked_facts(conn, ctx.case_id) if f.value]
        names = await party_names(conn, ctx.case_id)
        born = await services.dates_of_birth(conn, ctx.case_id)
    if not questions or not facts:
        return
    kit, embedder = services.ai(), services.embed()

    def stripped(text: str) -> pii.Stripped:
        return pii.strip(text, pack=pack, names=names, dates_of_birth=born)

    fact_vectors = embedder.embed([stripped(f"{f.kind} {f.value}") for f in facts])
    question_vectors = embedder.embed([stripped(words) for _, words in questions])
    pool = [(f.fact_id, vector, str(f.value)) for f, vector in zip(facts, fact_vectors, strict=True)]
    by_id = {f.fact_id: f for f in facts}
    offered: dict[uuid.UUID, list[uuid.UUID]] = {}
    for (question_id, words), vector in zip(questions, question_vectors, strict=True):
        offered[question_id] = links.candidates((vector, words), pool, k=TOP_K, reranker=services.reranker)
    shown = list(dict.fromkeys(f for ids in offered.values() for f in ids))
    text = ai_tasks.t11_text(questions, [(f, by_id[f].kind, _day(by_id[f].event_date), by_id[f].value) for f in shown])
    await ctx.progress(50, step="links")
    async with ctx.transaction() as conn:
        out = await kit.run(
            conn, ai_tasks.t11(pack), pack=pack, text=text, case_id=ctx.case_id, names=names, dates_of_birth=born
        )
        for fact_id, question_id, reason in ai_tasks.accept_links(out, offered):
            await conn.execute(
                "insert into app.fact_question_links (case_id, fact_id, question_id, source, status, reason)"
                " values (%s, %s, %s, 'ai', 'suggested', %s) on conflict (case_id, fact_id, question_id) do nothing",
                (ctx.case_id, fact_id, question_id, reason[:80] or None),
            )


@registry.kind("rerun_checks", interactive=False, changes_case=False)
async def rerun_checks(ctx: JobContext) -> None:
    """Work out availability, then run the named expert's checks on checked facts and write the findings."""
    async with ctx.transaction() as conn:
        pack = await _case_pack(conn, ctx.case_id)
        await write_availability(conn, ctx.case_id, pack)
        facts = await _checked_facts(conn, ctx.case_id)
        ran = configured_checks(pack.checks(), await _expert_settings(conn, ctx.case_id))
    await ctx.progress(30, step="checks")
    tables = _tables(pack, ran)
    hits: list[tuple[Mapping[str, Any], Hit]] = []
    for check in ran:
        labels = await _labels(ctx, pack, check, facts)
        hits += [(check, hit) for hit in evaluate(check, facts, labels=labels, tables=tables)]
    pages = {f.fact_id: f.page_id for f in facts}
    async with ctx.transaction() as conn:
        await _save(conn, ctx, pack, ran, hits, pages)
        await guidelines.rebaseline(conn, ctx.case_id, pack.guidelines())
