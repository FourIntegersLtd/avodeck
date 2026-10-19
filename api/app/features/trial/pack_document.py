"""What goes into the trial pack (screen 24): the sections the expert chose, from the served report only.

Page references in the served report (`[p.N]`) are shown with their trial page number where the match is
confirmed (`[<court bundle> p.T]`), marked "not in the <court bundle>" where the page was left out, and
marked "trial page to confirm" where the match still waits for the expert. The served report itself never
changes (rule 8): this is a copy for the witness box.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from psycopg import AsyncConnection

from app.core.contracts.facts import citable_facts
from app.core.contracts.served import ServedReport, latest_served
from app.core.packs import MergedPack
from app.features.trial import repo
from app.features.trial.pdfs import Document
from app.features.trial.ports import answers_of
from app.features.trial.services import TrialServices
from app.features.trial.words import word

SECTIONS = ("report", "chronology", "readiness", "joint_statement", "written_answers", "legal_tests", "guidance")
DEFAULT_SECTIONS = ["report", "chronology", "readiness", "joint_statement", "written_answers", "legal_tests"]
_REF = re.compile(r"\[p\.(\d+)\]")


@dataclass
class Built:
    document: Document
    report_version_id: uuid.UUID
    court_bundle_id: uuid.UUID | None
    sections: list[str]
    include_guidance: bool


def show_date(pack: MergedPack, value: date | None) -> str:
    if value is None:
        return word(pack.language, "undated")
    parts = {"D": f"{value.day:02d}", "M": f"{value.month:02d}", "Y": f"{value.year:04d}"}
    order = [ch for ch in pack.date_order.upper() if ch in parts] or ["Y", "M", "D"]
    return "/".join(parts[ch] for ch in order)


def page_ref(pack: MergedPack, refs: dict[int, repo.PageRef], page_no: int) -> str:
    """How a page reference reads in the trial pack."""
    term = pack.term("court_bundle")
    ref = refs.get(page_no)
    if ref is None:
        return f"[p.{page_no}]"
    if ref.status == "matched":
        return f"[{term} p.{ref.trial_page_no}]"
    if ref.status == "missing":
        return f"[p.{page_no}, {word(pack.language, 'not_in_bundle', court_bundle=term)}]"
    return f"[p.{page_no}, {word(pack.language, 'trial_page_to_confirm')}]"


def with_trial_refs(pack: MergedPack, refs: dict[int, repo.PageRef], text: str) -> str:
    return _REF.sub(lambda m: page_ref(pack, refs, int(m.group(1))), text)


def legal_tests(document: Document, text: str) -> None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            document.heading(stripped.lstrip("#").strip(), 2)
        elif stripped:
            document.para(stripped.lstrip("-* ").strip())


async def pack_document(
    conn: AsyncConnection,
    services: TrialServices,
    pack: MergedPack,
    case_id: uuid.UUID,
    settings: dict[str, Any] | None,
) -> Built | None:
    crypto = services.case_crypto()
    served = await latest_served(conn, crypto, case_id)
    if served is None:
        return None
    sections = list(settings["sections"]) if settings else list(DEFAULT_SECTIONS)
    include_guidance = bool(settings["include_guidance"]) if settings else False
    bundle = await repo.current_bundle(conn, case_id)
    refs = await repo.page_refs(conn, case_id)
    lang = pack.language
    doc = Document(word(lang, "trial_pack"))

    for section in sections:
        if section == "report":
            doc.section("report")
            doc.heading(word(lang, "report"))
            doc.paras(f"{p.para_no}. {with_trial_refs(pack, refs, p.text)}" for p in served.paragraphs)
        elif section == "chronology":
            doc.section("chronology")
            doc.heading(word(lang, "chronology"))
            facts = await citable_facts(conn, case_id, services.reads)
            numbers = await repo.page_numbers(conn, list({f.page_id for f in facts}))
            facts = sorted(facts, key=lambda f: (f.event_date or date.max, numbers.get(f.page_id, 0)))
            if not facts:
                doc.para(word(lang, "none_yet"))
            for fact in facts:
                page_no = numbers.get(fact.page_id, 0)
                doc.para(
                    word(
                        lang,
                        "fact",
                        date=show_date(pack, fact.event_date),
                        page=page_ref(pack, refs, page_no),
                        text=fact.value or "",
                    )
                )
        elif section == "readiness":
            doc.section("readiness")
            doc.heading(word(lang, "readiness"))
            sheet_list = await sheets(conn, services, case_id, served)
            if not sheet_list:
                doc.para(word(lang, "none_yet"))
            for i, sheet in enumerate(sheet_list):
                if i:
                    doc.page_break()
                doc.heading(word(lang, "sheet", number=sheet.number, text=sheet.text), 2)
                if sheet.pages:
                    doc.para(" ".join(page_ref(pack, refs, n) for n in sheet.pages))
                doc.para(word(lang, "my_answer"))
                doc.para(with_trial_refs(pack, refs, sheet.answer or word(lang, "none_yet")))
                doc.para(word(lang, "may_be_asked"))
                doc.para(with_trial_refs(pack, refs, sheet.asked or word(lang, "none_yet")))
                if sheet.reply:
                    doc.para(word(lang, "my_reply"))
                    doc.para(with_trial_refs(pack, refs, sheet.reply))
                if sheet.agreed is False and sheet.reason:
                    doc.para(word(lang, "not_agreed", text=sheet.reason))
        elif section == "joint_statement":
            doc.section("joint_statement")
            meeting = await repo.latest_meeting(conn, case_id)
            label = _meeting_label(pack, meeting)
            doc.heading(label)
            items = await repo.current_items(conn, crypto, case_id, meeting["id"]) if meeting else []
            if not items:
                doc.para(word(lang, "none_yet"))
            for item in items:
                doc.para(word(lang, "item", number=item["number"], text=item["text"]))
                if item["own_position"]:
                    doc.para(word(lang, "own_view", text=with_trial_refs(pack, refs, item["own_position"])))
                if item["agreed"] is True:
                    doc.para(word(lang, "agreed"))
                elif item["agreed"] is False:
                    doc.para(word(lang, "not_agreed", text=item["reason"] or ""))
        elif section == "written_answers":
            doc.section("written_answers")
            doc.heading(word(lang, "written_answers"))
            rounds = await services.answers.served(conn, crypto, case_id)
            if not answers_of(rounds):
                doc.para(word(lang, "none_yet"))
            for answered in rounds:
                doc.para(word(lang, "answers_round", number=answered.round_no))
                doc.paras(f"{a.question_no}. {with_trial_refs(pack, refs, a.text)}" for a in answered.answers)
        elif section == "legal_tests":
            text = pack.legal_tests()
            if text.strip():
                doc.section("legal_tests")
                doc.heading(word(lang, "legal_tests"))
                legal_tests(doc, text)
    if include_guidance or "guidance" in sections:
        guidance = pack.guidelines()
        if guidance:
            doc.section("guidance")
            doc.heading(word(lang, "guidance"))
            doc.paras(f"{g.get('code', '')}: {g.get('title', '')}".strip(": ") for g in guidance)
    return Built(doc, served.version_id, bundle["id"] if bundle else None, sections, include_guidance)


@dataclass(frozen=True)
class Sheet:
    """One readiness sheet: an agenda question, the expert's own answer, what the other expert said (what they
    may be asked about) and the pages the served report cites for it."""

    number: int
    text: str
    answer: str | None
    asked: str | None
    agreed: bool | None
    reason: str | None
    pages: list[int]
    reply: str | None = None
    replied_at: datetime | None = None


async def sheets(
    conn: AsyncConnection, services: TrialServices, case_id: uuid.UUID, served: ServedReport | None
) -> list[Sheet]:
    """A sheet for each question of the case's current agenda (from the served report and the statement only)."""
    meeting = await repo.latest_meeting(conn, case_id)
    if meeting is None:
        return []
    by_id = {p.id: p for p in served.paragraphs} if served else {}
    replies = await repo.replies(conn, services.case_crypto(), case_id)
    out = []
    for item in await repo.current_items(conn, services.case_crypto(), case_id, meeting["id"]):
        cited = sorted({no for pid in item["served_paragraph_ids"] if pid in by_id for _, no in by_id[pid].cited})
        out.append(
            Sheet(
                item["number"],
                item["text"],
                item["own_position"],
                item["other_positions"],
                item["agreed"],
                item["reason"],
                cited,
                *replies.get(item["number"], (None, None)),
            )
        )
    return out


def _meeting_label(pack: MergedPack, meeting: dict[str, Any] | None) -> str:
    types = pack.meeting_types()
    kind = next((t for t in types if meeting and t.get("key") == meeting["type"]), types[0] if types else None)
    return str(kind["label"]) if kind else word(pack.language, "joint_statement", meeting="")
