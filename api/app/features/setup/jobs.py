"""Slice C's worker jobs: `extract_questions` (T-06) and `prefill_case` (T-04).

`extract_questions` reads the case's latest letter: OCR, identifiers out (`pii.strip`, rule 7), T-06
through the AI kit, then the word check (rule 2). Each kept question or assumption stores the original
words of the run it cites, so the stored wording is always an exact piece of the letter; anything the
model rewrote is refused and counted. The paragraph reference is read from the letter's layout and the
"usually outside the expertise" flag from the pack, both in code. Rows start `suggested`, tied to the
instruction version. A newer letter on a case never served moves the earlier questions to the new
version: if it adds a question, a confirmed set reopens (they return to suggested, with a warning on
screen 06); if it adds none, the confirmed set stays confirmed.

`prefill_case` reads the same letter and asks T-04 for case details. A name is stored as the cited words
of the letter, never the model's value, and a run hidden by the identifier strip is refused; a key must be
one of the pack's and the cited words must carry the pack's word for it; a due date must be written out in
the cited words (read with the pack's date order and language). Kept suggestions are stored encrypted, waiting for the expert; nothing is
applied to the case.

No text reaches a log or a job row (rule 6).
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Mapping
from datetime import date
from typing import Any

from psycopg import AsyncConnection

from app.core.ai import pii, provenance
from app.core.crypto import CaseCrypto
from app.core.jobs import JobContext, registry
from app.core.packs import MergedPack
from app.core.storage import SupabaseStorage
from app.features.setup import ai_tasks
from app.features.setup.letter import Found, Letter, checked_items
from app.features.setup.services import current

logger = logging.getLogger("app.setup")

PARTY_NAME = "party_name"
PREFILL_VALUE = "prefill_value"
_ISO = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


async def case_names(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> list[str]:
    """The names of the case's parties, for `pii.strip`."""
    cur = await conn.execute("select name from app.case_parties where case_id = %s order by created_at, id", (case_id,))
    return [
        (await crypto.decrypt(conn, case_id, bytes(r[0]), purpose=PARTY_NAME)).decode() for r in await cur.fetchall()
    ]


async def read_document(
    conn: AsyncConnection, storage: SupabaseStorage, crypto: CaseCrypto, case_id: uuid.UUID, prefix: str
) -> bytes:
    parts = []
    for path in sorted(await storage.list(prefix)):
        parts.append(await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path))
    return b"".join(parts)


async def read_letter(ctx: JobContext, pack: MergedPack, document_prefix: str, names: list[str]) -> Letter:
    services = current()
    _, ocr = services.ai()
    storage, crypto = services.files()
    async with ctx.transaction() as conn:
        document = await read_document(conn, storage, crypto, ctx.case_id, document_prefix)
    letter = Letter.from_ocr(await ocr.analyse(document, "layout"))
    letter.mask(pack=pack, names=names, dates_of_birth=())
    return letter


async def _latest_letter(conn: AsyncConnection, case_id: uuid.UUID) -> tuple[uuid.UUID, int, str] | None:
    cur = await conn.execute(
        "select v.id, v.version_no, d.storage_path from app.instruction_versions v"
        " join app.documents d on d.id = v.document_id"
        " where v.case_id = %s and v.kind in ('letter', 'supplementary') order by v.version_no desc limit 1",
        (case_id,),
    )
    row = await cur.fetchone()
    if row is None or row[2] is None:
        return None
    return row[0], int(row[1]), str(row[2])


async def _case_pack(conn: AsyncConnection, case_id: uuid.UUID) -> MergedPack:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    assert row is not None
    return current().packs.combine(str(row[0]), str(row[1]), str(row[2]))


# --- extract_questions ----------------------------------------------------------------------------------


@registry.kind("extract_questions", interactive=True, changes_case=True)
async def extract_questions(ctx: JobContext) -> None:
    services = current()
    kit, _ = services.ai()
    _, crypto = services.files()
    async with ctx.transaction() as conn:
        latest = await _latest_letter(conn, ctx.case_id)
        if latest is None:
            return
        version_id, version_no, prefix = latest
        cur = await conn.execute(
            "select 1 from app.instruction_extractions where instruction_version_id = %s", (version_id,)
        )
        if await cur.fetchone() is not None:
            return
        pack = await _case_pack(conn, ctx.case_id)
        names = await case_names(conn, crypto, ctx.case_id)
    await ctx.progress(10, step="ocr")
    letter = await read_letter(ctx, pack, prefix, names)
    await ctx.progress(40, step="questions")
    task = ai_tasks.t06(pack)
    async with ctx.transaction() as conn:
        out = await kit.run(conn, task, pack=pack, text=letter.prompt_text(), case_id=ctx.case_id, names=names)
    questions, rejected_q = checked_items(letter, out["questions"])
    assumptions, rejected_a = checked_items(letter, out["assumptions"])
    outside = set(pack.outside_expertise_question_types())

    async def save(conn: AsyncConnection) -> None:
        served = await _ever_served(conn, ctx.case_id)
        known_q = await _known(conn, "questions", ctx.case_id)
        known_a = await _known(conn, "assumptions", ctx.case_id)
        new_q = [(i, f) for i, f in questions if f.text not in known_q]
        new_a = [(i, f) for i, f in assumptions if f.text not in known_a]
        cur = await conn.execute(
            "select locked_at is not null from app.instruction_versions where case_id = %s and version_no < %s"
            " order by version_no desc limit 1",
            (ctx.case_id, version_no),
        )
        prev = await cur.fetchone()
        was_locked = bool(prev and prev[0])
        reopened = was_locked and bool(new_q or new_a) and not served
        if not served:
            for table in ("questions", "assumptions"):
                status = "'suggested'" if reopened else "status"
                await conn.execute(
                    f"update app.{table} set instruction_version_id = %s, status = {status}"  # noqa: S608
                    " where case_id = %s and instruction_version_id is distinct from %s",
                    (version_id, ctx.case_id, version_id),
                )
        number = await _next_number(conn, "questions", ctx.case_id)
        for item, found in sorted(new_q, key=lambda q: q[1].start):
            kind = str(item["kind"])
            await conn.execute(
                "insert into app.questions (case_id, instruction_version_id, number, text_verbatim, source_word_ids,"
                " para_ref, kind, outside_expertise) values (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    ctx.case_id,
                    version_id,
                    number,
                    found.text,
                    found.word_ids,
                    letter.para_ref(found.start),
                    kind,
                    kind in outside,
                ),
            )
            number += 1
        number = await _next_number(conn, "assumptions", ctx.case_id)
        for _, found in sorted(new_a, key=lambda a: a[1].start):
            await conn.execute(
                "insert into app.assumptions (case_id, instruction_version_id, number, text_verbatim, source_word_ids,"
                " para_ref) values (%s, %s, %s, %s, %s, %s)",
                (ctx.case_id, version_id, number, found.text, found.word_ids, letter.para_ref(found.start)),
            )
            number += 1
        if was_locked and not new_q and not new_a and not served:
            await conn.execute("update app.instruction_versions set locked_at = now() where id = %s", (version_id,))
        await conn.execute(
            "insert into app.instruction_extractions (instruction_version_id, case_id, job_id, questions, assumptions,"
            " rejected, reopened, model_task, prompt_version) values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                version_id,
                ctx.case_id,
                ctx.job_id,
                len(new_q),
                len(new_a),
                len(rejected_q) + len(rejected_a),
                reopened,
                task.task_id,
                task.prompt_version,
            ),
        )

    await ctx.step("save_questions", save)
    if rejected_q or rejected_a:
        logger.info("T-06 refused %d suggestions on case %s", len(rejected_q) + len(rejected_a), ctx.case_id)


async def _ever_served(conn: AsyncConnection, case_id: uuid.UUID) -> bool:
    cur = await conn.execute("select app.case_ever_served(%s)", (case_id,))
    row = await cur.fetchone()
    return bool(row and row[0])


async def _known(conn: AsyncConnection, table: str, case_id: uuid.UUID) -> set[str]:
    cur = await conn.execute(f"select text_verbatim from app.{table} where case_id = %s", (case_id,))  # noqa: S608
    return {str(r[0]) for r in await cur.fetchall()}


async def _next_number(conn: AsyncConnection, table: str, case_id: uuid.UUID) -> int:
    cur = await conn.execute(f"select coalesce(max(number), 0) + 1 from app.{table} where case_id = %s", (case_id,))  # noqa: S608
    row = await cur.fetchone()
    return int(row[0]) if row else 1


# --- prefill_case (T-04) -------------------------------------------------------------------------------


_PLACEHOLDER = re.compile(r"\[[A-Z][A-Z0-9_]*_\d+\]")


def _labels(pack: MergedPack, field: str) -> dict[str, list[str]]:
    """Each key the pack offers for `field`, with the pack's words for it (rule 11)."""
    if field == "instructing_party_type":
        return {p["key"]: [p["label"]] for p in pack.instructing_party_types()}
    if field == "party_role":
        return {p["key"]: [p["label"]] for p in pack.party_roles()}
    if field == "court_track":
        words = pack.vocabulary.get("court_tracks")
        words = words if isinstance(words, dict) else {}
        return {
            str(k): [str(words.get(k) or str(k).replace("_", " "))]
            for k in pack.prompt_context("T-04").get("court_tracks", [])
        }
    return {}


def _same_word(cited: str, label: str) -> bool:
    """The pack's word, or that word with a short ending (a plural on a letterhead)."""
    return cited == label or (cited.startswith(label) and len(cited) - len(label) <= 2)


def _names_key(cited: list[str], label: list[str]) -> bool:
    size = len(label)
    return bool(label) and any(
        all(_same_word(c, w) for c, w in zip(cited[i : i + size], label, strict=True))
        for i in range(len(cited) - size + 1)
    )


def check_suggestion(letter: Letter, pack: MergedPack, item: Mapping[str, Any]) -> tuple[list[str], str] | None:
    """The cited word ids and the value to store when a suggestion passes, else None.

    Rule 2: a name is stored as the letter's own words (the cited run), never the model's value, and a
    run the identifier strip replaced (a placeholder) is refused. A key must be one of the pack's keys and
    the cited words must carry the pack's word for that key (rule 11). A due date must be written out.
    """
    field, value, word_ids = str(item["field"]), str(item["value"]), [str(w) for w in item["word_ids"]]
    if field == "instructing_party_name":
        if _PLACEHOLDER.search(value):
            return None
        found = letter.verify(value, word_ids)
        if not isinstance(found, Found):
            return None
        if any(letter.masked[w] != letter.words[letter.index[w]].text for w in found.word_ids):
            return None
        return found.word_ids, found.text
    if any(w not in letter.index for w in word_ids):
        return None
    positions = sorted({letter.index[w] for w in word_ids})
    if positions[-1] - positions[0] != len(positions) - 1:
        return None
    cited = " ".join(letter.words[i].text for i in range(positions[0], positions[-1] + 1))
    if field == "due_date":
        m = _ISO.match(value)
        if m is None:
            return None
        try:
            day = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
        written = list(pii._known_dates(cited, [day], pack.date_order, pack.language))
        return (word_ids, value) if written else None
    labels = _labels(pack, field).get(value)
    if labels is None:
        return None
    cited_atoms = provenance.atoms(cited)
    if not any(_names_key(cited_atoms, provenance.atoms(label)) for label in labels):
        return None
    return word_ids, value


@registry.kind("prefill_case", interactive=True, changes_case=False)
async def prefill_case(ctx: JobContext) -> None:
    services = current()
    kit, _ = services.ai()
    _, crypto = services.files()
    async with ctx.transaction() as conn:
        latest = await _latest_letter(conn, ctx.case_id)
        if latest is None:
            return
        version_id, _, prefix = latest
        pack = await _case_pack(conn, ctx.case_id)
        names = await case_names(conn, crypto, ctx.case_id)
    await ctx.progress(10, step="ocr")
    letter = await read_letter(ctx, pack, prefix, names)
    await ctx.progress(50, step="prefill")
    task = ai_tasks.t04(pack)
    async with ctx.transaction() as conn:
        out = await kit.run(conn, task, pack=pack, text=letter.prompt_text(), case_id=ctx.case_id, names=names)
    kept: list[tuple[Mapping[str, Any], list[str], str]] = []
    for item in out["suggestions"]:
        checked = check_suggestion(letter, pack, item)
        if checked is not None:
            kept.append((item, *checked))
    rejected = len(out["suggestions"]) - len(kept)

    async def save(conn: AsyncConnection) -> None:
        for item, cited, value in kept:
            sealed = await crypto.encrypt(conn, ctx.case_id, value.encode(), purpose=PREFILL_VALUE)
            await conn.execute(
                "insert into app.prefill_suggestions (case_id, instruction_version_id, field, value, word_ids,"
                " model_task, prompt_version) values (%s, %s, %s, %s, %s, %s, %s) on conflict do nothing",
                (ctx.case_id, version_id, item["field"], sealed, cited, task.task_id, task.prompt_version),
            )
        await conn.execute(
            "insert into app.prefill_runs (case_id, job_id, kept, rejected) values (%s, %s, %s, %s)",
            (ctx.case_id, ctx.job_id, len(kept), rejected),
        )

    await ctx.step("save_prefill", save)
    if rejected:
        logger.info("T-04 refused %d suggestions on case %s", rejected, ctx.case_id)
