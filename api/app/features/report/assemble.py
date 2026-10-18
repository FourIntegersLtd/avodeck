"""What goes into a report, section by section, before it is laid out in Word and PDF.

Section order: the sections the expert mapped to headings in their own template, in the template's
order, then every other pack section and appendix in pack order. Each section's words come from one
provider: opinions (slice E's latest opinions, by question), declarations (the jurisdiction pack's
wording plus the expert's additions, locked), the generated appendices (methodology, chronology, list of
documents) or the expert's own section words (`report_section_texts`).

Citations (rule 2): each `[[cite:n]]` marker names the n-th saved citation. It renders as a page
reference (`[p.N]`, the bundle page number) or a numbered reference (`[k]`, with a references list) only
when its fact is citable now and still holds the words quoted; otherwise it renders as `[?]` and no
citation is recorded, and the final check fails on it.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from psycopg import AsyncConnection

from app.core.contracts import ai_calls, opinions, profile, questions
from app.core.contracts.citations import Citation, differs
from app.core.contracts.facts import Fact, citable_facts
from app.core.contracts.reading import PageReadLog
from app.core.crypto import CaseCrypto
from app.core.packs import MergedPack
from app.features.report.words import word

OPINION = "opinion"
DECLARATIONS = "declarations"
METHODOLOGY = "methodology"
CHRONOLOGY = "chronology"
DOCUMENTS_LIST = "documents_list"
REFERENCES = "references"
GENERATED = frozenset({OPINION, DECLARATIONS, METHODOLOGY, CHRONOLOGY, DOCUMENTS_LIST})
SECTION_PURPOSE = "report_section_text"
PARAGRAPH_PURPOSE = "report_paragraph"
VALUE_PURPOSE = "report_citation_value"
QUOTE_PURPOSE = "report_citation_quote"

_MARKER = re.compile(r"\[\[cite:(\d+)\]\]")


@dataclass
class Cited:
    cite_no: int
    fact: Fact
    quote: str
    page_no: int


@dataclass
class Para:
    text: str
    question_id: uuid.UUID | None = None
    cites: list[Cited] = field(default_factory=list)


@dataclass
class Block:
    key: str
    paragraphs: list[Para]
    locked: bool = False


@dataclass
class Assembly:
    style: str
    language: str
    blocks: list[Block]

    def citations(self) -> list[Cited]:
        return [c for b in self.blocks for p in b.paragraphs for c in p.cites]


@dataclass(frozen=True)
class Methodology:
    pages_read: int
    pages_total: int
    tasks: list[ai_calls.AiCallSummary]


def appendices(pack: MergedPack) -> list[str]:
    """The case type's appendices. Packs have no public accessor yet (foundation request in the brief)."""
    return [str(a["key"]) for a in pack.report_appendices()]


def section_keys(pack: MergedPack) -> list[str]:
    return [str(s["key"]) for s in pack.report_sections()] + appendices(pack)


def writable_keys(pack: MergedPack) -> list[str]:
    """The sections the expert writes in their own words on screen 16."""
    return [k for k in section_keys(pack) if k not in GENERATED]


def template_order(keys: Sequence[str], template: profile.ReportTemplate | None) -> list[str]:
    """Mapped sections in the template's heading order, then the rest in pack order."""
    mapped = template_mapping(keys, template)
    first = sorted(mapped, key=lambda k: mapped[k])
    return first + [k for k in keys if k not in mapped]


def template_mapping(keys: Sequence[str], template: profile.ReportTemplate | None) -> dict[str, int]:
    if template is None:
        return {}
    out: dict[str, int] = {}
    for item in template.mapping:
        if isinstance(item, dict) and item.get("section") in keys and isinstance(item.get("index"), int):
            out.setdefault(str(item["section"]), int(item["index"]))
    return out


async def methodology(conn: AsyncConnection, case_id: uuid.UUID, reads: PageReadLog | None) -> Methodology:
    """Pages the named expert has read out of the case's pages, and each AI task's metadata (no content)."""
    cur = await conn.execute("select id from app.pages where case_id = %s", (case_id,))
    pages = {r[0] for r in await cur.fetchall()}
    read: set[uuid.UUID] = set()
    if reads is not None:
        cur = await conn.execute("select named_expert_id from app.cases where id = %s", (case_id,))
        row = await cur.fetchone()
        if row is not None:
            read = await reads.read_page_ids(conn, case_id, row[0]) & pages
    return Methodology(len(read), len(pages), await ai_calls.ai_call_summary(conn, case_id))


def _day(value: date | datetime, pack: MergedPack) -> str:
    d = value.date() if isinstance(value, datetime) else value
    parts = {"D": f"{d.day:02d}", "M": f"{d.month:02d}", "Y": f"{d.year:04d}"}
    order = pack.date_order.upper()
    return "/".join(parts[c] for c in order if c in parts) if set(order) >= {"D", "M", "Y"} else d.isoformat()


class _Citer:
    def __init__(self, style: str, citable: Sequence[Fact], page_nos: dict[uuid.UUID, int]) -> None:
        self.style = style
        self.citable = {f.id: f for f in citable}
        self.page_nos = page_nos
        self.next_no = 1

    def render(self, body: str, refs: Sequence[Citation], question_id: uuid.UUID | None = None) -> list[Para]:
        out: list[Para] = []
        for chunk in [c.strip() for c in re.split(r"\n\s*\n", body) if c.strip()]:
            para = Para(text="", question_id=question_id)

            def replace(match: re.Match[str], para: Para = para) -> str:
                n = int(match.group(1))
                citation = refs[n - 1] if 0 < n <= len(refs) else None
                fact = self.citable.get(citation.fact_id) if citation is not None else None
                if citation is None or fact is None or differs(citation, fact) or fact.page_id not in self.page_nos:
                    return "[?]"
                cited = Cited(self.next_no, fact, citation.quote_verbatim, self.page_nos[fact.page_id])
                self.next_no += 1
                para.cites.append(cited)
                return f"[p.{cited.page_no}]" if self.style == "page" else f"[{cited.cite_no}]"

            para.text = _MARKER.sub(replace, " ".join(chunk.split()))
            out.append(para)
        return out


async def section_texts(
    conn: AsyncConnection, case_id: uuid.UUID, crypto: CaseCrypto
) -> dict[str, tuple[str, list[Citation]]]:
    cur = await conn.execute(
        "select section, body, citation_refs from app.report_section_texts where case_id = %s", (case_id,)
    )
    out: dict[str, tuple[str, list[Citation]]] = {}
    for key, body, refs in await cur.fetchall():
        text = (await crypto.decrypt(conn, case_id, bytes(body), purpose=SECTION_PURPOSE)).decode()
        out[str(key)] = (text, [Citation.model_validate(r) for r in refs])
    return out


async def assemble(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    pack: MergedPack,
    reads: PageReadLog | None,
    crypto: CaseCrypto,
    template: profile.ReportTemplate | None,
    style: str,
) -> Assembly:
    language = pack.language
    cur = await conn.execute("select id, bundle_page_no from app.pages where case_id = %s", (case_id,))
    page_nos = {r[0]: int(r[1]) for r in await cur.fetchall()}
    citable = await citable_facts(conn, case_id, reads)
    citer = _Citer(style, citable, page_nos)
    texts = await section_texts(conn, case_id, crypto)
    blocks: list[Block] = []
    for key in template_order(section_keys(pack), template):
        if key == OPINION:
            paragraphs = await _opinions(conn, case_id, crypto, citer, language)
        elif key == DECLARATIONS:
            blocks.append(Block(key, await _declarations(conn, case_id, pack), locked=True))
            continue
        elif key == METHODOLOGY:
            paragraphs = _methodology(await methodology(conn, case_id, reads), language)
        elif key == CHRONOLOGY:
            paragraphs = _chronology(citable, page_nos, pack)
        elif key == DOCUMENTS_LIST:
            paragraphs = await _documents(conn, case_id, language)
        elif key in texts:
            paragraphs = citer.render(*texts[key])
        else:
            paragraphs = []
        blocks.append(Block(key, paragraphs))
    if style == "paragraph":
        listed = [c for b in blocks for p in b.paragraphs for c in p.cites]
        if listed:
            refs = [Para(word(language, "reference", n=c.cite_no, page=c.page_no)) for c in listed]
            blocks.append(Block(REFERENCES, refs))
    return Assembly(style=style, language=language, blocks=blocks)


async def _opinions(
    conn: AsyncConnection, case_id: uuid.UUID, crypto: CaseCrypto, citer: _Citer, language: str
) -> list[Para]:
    asked = {q.id: q for q in await questions.confirmed_questions(conn, case_id)}
    out: list[Para] = []
    for opinion in await opinions.latest_opinions(conn, case_id, crypto):
        question = asked.get(opinion.question_id)
        if question is None:
            continue
        out.append(Para(f"{word(language, 'question', n=question.number)}: {question.wording}", question.id))
        out.extend(citer.render(opinion.body, opinion.citations, question.id))
    return out


async def _declarations(conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack) -> list[Para]:
    out: list[Para] = []
    for declaration in pack.declarations():
        out.extend(Para(" ".join(c.split())) for c in re.split(r"\n\s*\n", declaration["text"]) if c.strip())
    for added in await profile.declarations(conn, case_id):
        if added.text:
            out.append(Para(" ".join(added.text.split())))
    return out


def _methodology(found: Methodology, language: str) -> list[Para]:
    out = [Para(word(language, "pages_read", read=found.pages_read, total=found.pages_total))]
    for t in found.tasks:
        out.append(
            Para(
                word(
                    language,
                    "task",
                    task=t.task_id,
                    model=t.model_id,
                    region=t.region,
                    prompt=t.prompt_version,
                    calls=t.calls,
                    first=t.first_at.date().isoformat(),
                    last=t.last_at.date().isoformat(),
                )
            )
        )
    if not found.tasks:
        out.append(Para(word(language, "no_tasks")))
    return out


def _chronology(citable: Sequence[Fact], page_nos: dict[uuid.UUID, int], pack: MergedPack) -> list[Para]:
    dated = sorted((f for f in citable if f.event_date is not None), key=lambda f: (f.event_date, f.created_at))
    return [
        Para(f"{_day(f.event_date, pack)} {' '.join((f.value or '').split())} [p.{page_nos[f.page_id]}]")
        for f in dated
        if f.event_date is not None and f.page_id in page_nos
    ]


async def _documents(conn: AsyncConnection, case_id: uuid.UUID, language: str) -> list[Para]:
    cur = await conn.execute(
        "select d.filename, min(p.bundle_page_no), max(p.bundle_page_no) from app.documents d"
        " join app.pages p on p.document_id = d.id where d.case_id = %s and p.duplicate_of_page_id is null"
        " group by d.id, d.filename order by min(p.bundle_page_no)",
        (case_id,),
    )
    rows: list[Any] = await cur.fetchall()
    return [Para(word(language, "document", name=name, first=first, last=last)) for name, first, last in rows]
