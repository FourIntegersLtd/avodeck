"""Slice F's final check (screen 17): the core checks plus the case's pack checks, run on the latest draft.

Each check gives an outcome (`passed`, `failed`, or `not_run` when Avodeck cannot check it yet) and, when it
fails, "Fix it" targets: the screen (its board key) and the item to open there (a question, page number,
finding, section or setting). Results carry check keys, codes and ids only, never report or record words
(rule 6). Pack checks come from `final_checks.yaml` in the case's packs (rule 11); a pack check this code
does not know fails as `unknown_check` so a new pack rule never passes silently.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from psycopg import AsyncConnection

from app.core.ai.kit import AiKit, InvalidOutput
from app.core.contracts import opinions, profile, questions
from app.core.contracts.case_identity import identity_for
from app.core.contracts.citations import CitationProblem, citation_problems, markers, quote_within
from app.core.contracts.facts import Fact, citable_facts
from app.core.contracts.reading import PageReadLog
from app.core.crypto import CaseCrypto
from app.core.errors import ProcessingPaused
from app.core.packs import MergedPack
from app.features.report import ai_tasks
from app.features.report.assemble import (
    DECLARATIONS,
    OPINION,
    PARAGRAPH_PURPOSE,
    QUOTE_PURPOSE,
    VALUE_PURPOSE,
    section_texts,
    writable_keys,
)
from app.features.report.words import months

CORE = (
    "report_built",
    "draft_current",
    "questions_locked",
    "citations_resolve",
    "facts_unchanged_since_cited",
    "quotes_verbatim",
    "all_pages_read",
    "findings_decided",
    "dates_cited",
    "no_provisional_opinions",
    "sections_written",
    "lint_dismissals_explained",
    "conflicts_declared",
    "expert_details_complete",
    "signature_ready",
    "declaration_acknowledged",
    "age_consistent",
)
MAX_FIXES = 50

_NUMERIC = re.compile(r"\b(\d{4}-\d{1,2}-\d{1,2}|\d{1,2}[/.-]\d{1,2}[/.-](\d{4}|\d{2}))\b")


def mentions_date(text: str, language: str) -> bool:
    """Whether the words contain a written date (numbers with a year, or a month name with a day or year)."""
    plain = re.sub(r"\[\[cite:\d+\]\]", " ", text)
    if _NUMERIC.search(plain):
        return True
    names = "|".join(sorted(map(re.escape, months(language)), key=len, reverse=True))
    day = r"\d{1,2}(?:st|nd|rd|th)?"
    patterns = (
        rf"\b{day}\s+(?:{names})\.?(?:\s+\d{{2,4}})?\b",
        rf"\b(?:{names})\.?\s+{day},?\s+\d{{4}}\b",
        rf"\b(?:{names})\.?\s+\d{{4}}\b",
    )
    return any(re.search(p, plain, re.IGNORECASE) for p in patterns)


def declaration_version(pack: MergedPack) -> str:
    """A fingerprint of the pack's declaration wording; a correction changes it (F34)."""
    joined = "\n".join(f"{d['key']}@{d.get('version', '')}\n{d['text']}" for d in pack.declarations())
    return hashlib.sha256(joined.encode()).hexdigest()[:32]


def declaration_paragraphs(pack: MergedPack) -> list[str]:
    """The pack's declarations split into paragraphs as the report lays them out."""
    return [" ".join(c.split()) for d in pack.declarations() for c in re.split(r"\n\s*\n", d["text"]) if c.strip()]


def pack_file(pack: MergedPack, name: str) -> dict[str, Any]:
    # Foundation request (slice F brief): accessors for report.yaml page_limit and report_sections.yaml
    # uses_points_against; until then read the merged file.
    files: dict[str, Any] = getattr(pack, "_files", {})
    content = files.get(name, {})
    return content if isinstance(content, dict) else {}


def fix(screen: str, item: object) -> dict[str, str]:
    return {"screen": screen, "item": str(item)}


@dataclass
class Result:
    key: str
    source: str
    outcome: str = "passed"
    fixes: list[dict[str, str]] = field(default_factory=list)
    text: str | None = None
    code: str | None = None
    explain: str | None = None
    checked: int | None = None
    total: int | None = None
    unit: str | None = None

    def counted(self, checked: int, unit: str, total: int | None = None) -> Result:
        self.checked, self.unit, self.total = checked, unit, total
        return self

    def failing(self, fixes: Sequence[dict[str, str]]) -> Result:
        unique = list({(f["screen"], f["item"]): f for f in fixes}.values())
        if unique:
            self.outcome, self.fixes = "failed", unique
        return self

    def out(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "key": self.key,
            "source": self.source,
            "outcome": self.outcome,
            "count": len(self.fixes),
            "fix": self.fixes[0] if self.fixes else None,
            "fixes": self.fixes[:MAX_FIXES],
        }
        if self.text is not None:
            data["text"] = self.text
        if self.code is not None:
            data["code"] = self.code
        if self.explain is not None:
            data["explain"] = self.explain
        if self.unit is not None:
            data.update(checked=self.checked, total=self.total, unit=self.unit)
        return data


@dataclass
class Paragraph:
    id: uuid.UUID
    para_no: int
    section: str
    question_id: uuid.UUID | None
    text: str


@dataclass
class DraftCitation:
    paragraph: Paragraph
    fact_id: uuid.UUID
    fact_version: int
    page_id: uuid.UUID
    value: str
    quote: str


@dataclass
class Inputs:
    conn: AsyncConnection
    case_id: uuid.UUID
    pack: MergedPack
    crypto: CaseCrypto
    reads: PageReadLog | None
    version_id: uuid.UUID
    version_at: datetime
    page_count: int | None
    paragraphs: list[Paragraph]
    citations: list[DraftCitation]
    citable: dict[uuid.UUID, Fact]
    problems: list[CitationProblem]
    opinions: list[opinions.OpinionText]
    questions: list[questions.Question]
    sections: dict[str, str]
    addressed: list[str]
    acknowledged: str | None
    court_track: str | None
    named_expert: uuid.UUID

    def where(self, paragraph: Paragraph) -> dict[str, str]:
        if paragraph.question_id is not None:
            return fix("S15", paragraph.question_id)
        return fix("S16", paragraph.section)


async def gather(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    pack: MergedPack,
    crypto: CaseCrypto,
    reads: PageReadLog | None,
    version_id: uuid.UUID,
) -> Inputs:
    cur = await conn.execute(
        "select v.created_at, b.page_count from app.report_versions v"
        " left join app.report_builds b on b.report_version_id = v.id where v.id = %s",
        (version_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    version_at, page_count = row
    cur = await conn.execute(
        "select id, para_no, section, question_id, text from app.report_paragraphs"
        " where report_version_id = %s order by para_no",
        (version_id,),
    )
    paragraphs = [
        Paragraph(
            pid, no, section, qid, (await crypto.decrypt(conn, case_id, bytes(t), purpose=PARAGRAPH_PURPOSE)).decode()
        )
        for pid, no, section, qid, t in await cur.fetchall()
    ]
    by_id = {p.id: p for p in paragraphs}
    cur = await conn.execute(
        "select paragraph_id, fact_id, fact_version, page_id, value_snapshot, quote_verbatim from app.citations"
        " where report_version_id = %s order by cite_no",
        (version_id,),
    )
    citations = [
        DraftCitation(
            by_id[pid],
            fid,
            version,
            page,
            (await crypto.decrypt(conn, case_id, bytes(value), purpose=VALUE_PURPOSE)).decode(),
            (await crypto.decrypt(conn, case_id, bytes(quote), purpose=QUOTE_PURPOSE)).decode(),
        )
        for pid, fid, version, page, value, quote in await cur.fetchall()
    ]
    cur = await conn.execute(
        "select c.court_track, c.named_expert_id, s.addressed_contents, s.acknowledged_declaration_version"
        " from app.cases c left join app.report_settings s on s.case_id = c.id where c.id = %s",
        (case_id,),
    )
    case_row = await cur.fetchone()
    assert case_row is not None
    track, expert, addressed, acknowledged = case_row
    return Inputs(
        conn=conn,
        case_id=case_id,
        pack=pack,
        crypto=crypto,
        reads=reads,
        version_id=version_id,
        version_at=version_at,
        page_count=page_count,
        paragraphs=paragraphs,
        citations=citations,
        citable={f.id: f for f in await citable_facts(conn, case_id, reads)},
        problems=await citation_problems(conn, case_id, reads),
        opinions=await opinions.latest_opinions(conn, case_id, crypto),
        questions=await questions.confirmed_questions(conn, case_id),
        sections={k: body for k, (body, _) in (await section_texts(conn, case_id, crypto)).items()},
        addressed=list(addressed or []),
        acknowledged=acknowledged,
        court_track=track,
        named_expert=expert,
    )


# core checks -------------------------------------------------------------------------------------------------


async def _report_built(i: Inputs, r: Result) -> Result:
    return r.failing([] if i.paragraphs and i.page_count is not None else [fix("S16", "build")])


async def _draft_current(i: Inputs, r: Result) -> Result:
    cur = await i.conn.execute(
        "select greatest((select max(updated_at) from app.opinions where case_id = %s),"
        " (select max(updated_at) from app.report_section_texts where case_id = %s))",
        (i.case_id, i.case_id),
    )
    row = await cur.fetchone()
    changed = row[0] if row else None
    return r.failing([fix("S16", "build")] if changed is not None and changed > i.version_at else [])


async def _questions_locked(i: Inputs, r: Result) -> Result:
    return r.failing([] if await questions.questions_locked(i.conn, i.case_id) else [fix("S06", "questions")])


async def _citations_resolve(i: Inputs, r: Result) -> Result:
    fixes = [fix("S15", p.question_id) for p in i.problems if p.kind in ("not_citable", "missing")]
    fixes += [i.where(c.paragraph) for c in i.citations if c.fact_id not in i.citable]
    return r.counted(len({c.fact_id for c in i.citations}), "fact").failing(fixes)


async def _facts_unchanged(i: Inputs, r: Result) -> Result:
    fixes = [fix("S15", p.question_id) for p in i.problems if p.kind == "changed_since_cited"]
    for c in i.citations:
        fact = i.citable.get(c.fact_id)
        if fact is not None and (
            fact.version != c.fact_version or fact.page_id != c.page_id or not quote_within(c.quote, fact.value)
        ):
            fixes.append(i.where(c.paragraph))
    return r.counted(max(0, len(i.citations) - len(fixes)), "fact", len(i.citations)).failing(fixes)


async def _quotes_verbatim(i: Inputs, r: Result) -> Result:
    return r.counted(len(i.citations), "quote").failing(
        [i.where(c.paragraph) for c in i.citations if not quote_within(c.quote, c.value)]
    )


async def _all_pages_read(i: Inputs, r: Result) -> Result:
    cur = await i.conn.execute(
        "select id, bundle_page_no from app.pages"
        " where bundle_id = (select bundle_id from app.cases where id = %s) order by bundle_page_no",
        (i.case_id,),
    )
    pages = await cur.fetchall()
    read = await i.reads.read_page_ids(i.conn, i.case_id, i.named_expert) if i.reads is not None else set()
    unread = [fix("S09", no) for pid, no in pages if pid not in read]
    return r.counted(len(pages) - len(unread), "page", len(pages)).failing(unread)


async def _findings_decided(i: Inputs, r: Result) -> Result:
    cur = await i.conn.execute(
        "select id from app.findings where case_id = %s and status = 'open' and decision is null"
        " order by created_at, id",
        (i.case_id,),
    )
    return r.failing([fix("S13", row[0]) for row in await cur.fetchall()])


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n", text) if p.strip()]


async def _dates_cited(i: Inputs, r: Result) -> Result:
    language = i.pack.language
    fixes = [
        fix("S15", o.question_id)
        for o in i.opinions
        for p in _paragraphs(o.body)
        if mentions_date(p, language) and not markers(p)
    ]
    fixes += [
        fix("S16", key)
        for key, body in i.sections.items()
        for p in _paragraphs(body)
        if mentions_date(p, language) and not markers(p)
    ]
    return r.failing(fixes)


async def _no_provisional(i: Inputs, r: Result) -> Result:
    return r.failing([fix("S15", o.question_id) for o in i.opinions if o.provisional])


async def _sections_written(i: Inputs, r: Result) -> Result:
    return r.failing([fix("S16", k) for k in writable_keys(i.pack) if not i.sections.get(k, "").strip()])


async def _lint_dismissals(i: Inputs, r: Result) -> Result:
    cur = await i.conn.execute(
        "select o.question_id from app.lint_issues l left join app.opinions o on o.id = l.opinion_id"
        " where l.case_id = %s and l.status = 'dismissed' and l.dismissed_reason is null",
        (i.case_id,),
    )
    return r.failing([fix("S15", q) if q else fix("S16", "lint") for (q,) in await cur.fetchall()])


async def _conflicts_declared(i: Inputs, r: Result) -> Result:
    declared = await profile.conflict_declaration(i.conn, i.case_id)
    return r.failing([fix("S05", "conflicts")] if declared is None or declared.out_of_date else [])


async def named_expert(conn: AsyncConnection, case_id: uuid.UUID) -> dict[str, Any] | None:
    expert = await profile.named_expert(conn, case_id)
    return dataclasses.asdict(expert) if expert is not None else None


async def _expert_details(i: Inputs, r: Result) -> Result:
    details = await named_expert(i.conn, i.case_id)
    complete = details is not None and all(
        str(details[k] or "").strip() for k in ("full_name", "registration_body", "registration_number")
    )
    return r.failing([] if complete else [fix("S02", "profile")])


async def _signature_ready(i: Inputs, r: Result) -> Result:
    return r.failing([] if await profile.signature(i.conn, i.case_id) is not None else [fix("S02", "signature")])


async def _declaration_acknowledged(i: Inputs, r: Result) -> Result:
    changed = i.acknowledged is not None and i.acknowledged != declaration_version(i.pack)
    return r.failing([fix("S18", "declaration")] if changed else [])


async def _age_consistent(i: Inputs, r: Result) -> Result:
    # No date of birth reaches slice F yet (contract request in the slice F brief).
    r.outcome, r.code = "not_run", "no_date_of_birth"
    return r


# pack checks -------------------------------------------------------------------------------------------------


async def _statement_exact(i: Inputs, r: Result) -> Result:
    drafted = [" ".join(p.text.split()) for p in i.paragraphs if p.section == DECLARATIONS]
    missing = [p for p in declaration_paragraphs(i.pack) if p not in drafted]
    return r.failing([fix("S16", DECLARATIONS)] if missing else [])


async def _required_contents(i: Inputs, r: Result) -> Result:
    items = [str(item["key"]) for item in i.pack.compliance_checklist()["items"]]
    return r.failing([fix("S16", key) for key in items if key not in i.addressed])


async def _page_limit(i: Inputs, r: Result) -> Result:
    limits = pack_file(i.pack, "report.yaml").get("page_limit") or {}
    limit = limits.get(i.court_track) if i.court_track and isinstance(limits, dict) else None
    over = limit is not None and i.page_count is not None and i.page_count > int(limit)
    return r.failing([fix("S16", "page_limit")] if over else [])


async def _questions_answered(i: Inputs, r: Result) -> Result:
    answered = {o.question_id for o in i.opinions if o.body.strip()}
    return r.counted(len(i.questions), "question").failing(
        [fix("S15", q.id) for q in i.questions if q.id not in answered]
    )


async def _points_considered(i: Inputs, r: Result) -> Result:
    if not pack_file(i.pack, "report_sections.yaml").get("uses_points_against"):
        return r
    cur = await i.conn.execute(
        "select distinct p.question_id, q.number from app.points p join app.questions q on q.id = p.question_id"
        " where p.case_id = %s and p.status = 'suggested' and q.status = 'confirmed' order by q.number",
        (i.case_id,),
    )
    return r.counted(len(i.questions), "question").failing([fix("S14", row[0]) for row in await cur.fetchall()])


def _day(value: object) -> date | None:
    return None if value in (None, "") else date.fromisoformat(str(value))


def _in_force(rows: Sequence[dict[str, Any]], on: date) -> bool:
    for row in rows:
        start, end = _day(row.get("in_force_from")), _day(row.get("in_force_to"))
        if start is not None and start <= on and (end is None or on < end):
            return True
    return False


async def _guidance_in_force(i: Inputs, r: Result) -> Result:
    by_code: dict[str, list[dict[str, Any]]] = {}
    for row in i.pack.guidelines():
        by_code.setdefault(str(row["code"]), []).append(row)
    fixes = []
    applied: set[str] = set()
    for o in i.opinions:
        named = [c for c in by_code if re.search(rf"(?<![\w]){re.escape(c)}(?![\w])", o.body)]
        applied.update(named)
        dates = [f.event_date for c in o.citations if (f := i.citable.get(c.fact_id)) and f.event_date]
        if any(not _in_force(by_code[c], d) for c in named for d in dates):
            fixes.append(fix("S15", o.question_id))
    return r.counted(len(applied), "guideline").failing(fixes)


Check = Callable[[Inputs, Result], Awaitable[Result]]
CORE_CHECKS: dict[str, Check] = {
    "report_built": _report_built,
    "draft_current": _draft_current,
    "questions_locked": _questions_locked,
    "citations_resolve": _citations_resolve,
    "facts_unchanged_since_cited": _facts_unchanged,
    "quotes_verbatim": _quotes_verbatim,
    "all_pages_read": _all_pages_read,
    "findings_decided": _findings_decided,
    "dates_cited": _dates_cited,
    "no_provisional_opinions": _no_provisional,
    "sections_written": _sections_written,
    "lint_dismissals_explained": _lint_dismissals,
    "conflicts_declared": _conflicts_declared,
    "expert_details_complete": _expert_details,
    "signature_ready": _signature_ready,
    "declaration_acknowledged": _declaration_acknowledged,
    "age_consistent": _age_consistent,
}
PACK_CHECKS: dict[str, Check] = {
    "statement_of_truth_exact": _statement_exact,
    "required_contents_present": _required_contents,
    "page_limit": _page_limit,
    "questions_answered": _questions_answered,
    "points_against_considered": _points_considered,
    "guidance_in_force": _guidance_in_force,
}


async def run_checks(i: Inputs) -> list[Result]:
    results = [await CORE_CHECKS[key](i, Result(key, "core")) for key in CORE]
    for entry in i.pack.final_checks():
        key = str(entry["key"])
        result = Result(
            key, "pack", text=str(entry.get("text") or "") or None, explain=str(entry.get("explain") or "") or None
        )
        check = PACK_CHECKS.get(key)
        if check is None:
            result.outcome, result.code = "failed", "unknown_check"
            results.append(result)
            continue
        results.append(await check(i, result))
    return results


# T-17 --------------------------------------------------------------------------------------------------------


def _expert_paragraphs(i: Inputs) -> list[Paragraph]:
    """The expert's own paragraphs: opinions (without each question's heading line) and written sections."""
    own = set(writable_keys(i.pack))
    seen: set[uuid.UUID] = set()
    out = []
    for p in i.paragraphs:
        if p.section == OPINION and p.question_id is not None and p.question_id not in seen:
            seen.add(p.question_id)  # "Question n: ..." is the instructing party's wording
            continue
        if p.section == OPINION or p.section in own:
            out.append(p)
    return out


def _span(words: str, text: str) -> tuple[int, int] | None:
    parts = words.split()
    if not parts:
        return None
    found = re.search(r"\s+".join(map(re.escape, parts)), text, re.IGNORECASE)
    return (found.start(), found.end()) if found else None


async def tone_warnings(i: Inputs, kit: AiKit | None) -> tuple[list[dict[str, Any]], str]:
    """T-17 over the expert's own paragraphs: warnings as places (paragraph, offsets, kind), never words."""
    paragraphs = _expert_paragraphs(i)
    if not paragraphs:
        return [], "done"
    if kit is None:
        return [], "not_run"
    # Foundation request: give T-17 the T-15a prompt context; until then it rides at the head of the text.
    context = json.dumps(i.pack.prompt_context("T-15a"), ensure_ascii=False, sort_keys=True)
    lines = [f"context\t{context}", *(f"sentence\t{n}\t{' '.join(p.text.split())}" for n, p in enumerate(paragraphs))]
    task = ai_tasks.t17(i.pack, len(paragraphs))
    who = await identity_for(i.conn, i.crypto, i.case_id)
    try:
        answer = await kit.run(
            i.conn,
            task,
            pack=i.pack,
            text="\n".join(lines),
            case_id=i.case_id,
            names=who.names,
            dates_of_birth=who.dates_of_birth,
            subject=f"report:{i.version_id}",
        )
    except (InvalidOutput, ProcessingPaused):
        return [], "not_run"
    out: list[dict[str, Any]] = []
    seen: set[tuple[int, str, int]] = set()
    for issue in answer.get("issues", []):
        index = int(issue["sentence_index"])
        if not 0 <= index < len(paragraphs):
            continue
        p = paragraphs[index]
        span = _span(str(issue["words"]), p.text)
        if span is None or (p.para_no, str(issue["type"]), span[0]) in seen:
            continue
        seen.add((p.para_no, str(issue["type"]), span[0]))
        out.append(
            {
                "paragraph_id": str(p.id),
                "para_no": p.para_no,
                "section": p.section,
                "question_id": str(p.question_id) if p.question_id else None,
                "type": str(issue["type"]),
                "start": span[0],
                "end": span[1],
            }
        )
    return out, "done"
