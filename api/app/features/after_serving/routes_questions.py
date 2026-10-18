"""Screen 20: written questions from the other side, their answers and the signed answers document.

Each round of questions has its own deadline and answers document. T-20a reads the questions as exact
words of their document and T-20b suggests a classification and linked paragraphs (the job in `jobs.py`);
nothing is decided until the expert says so. Answers cite only `citable_facts()` (rule 2). Signing the
answers is step-up (rule 10) and locks a new report version of type `answers`, linked to the served report;
serving it (slice F's route) serves the round.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Response
from pydantic import BaseModel, Field

from app.core import audit
from app.core.contracts.citations import Citation
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.after_serving import docs
from app.features.after_serving.common import (
    ANSWER,
    QUESTION,
    Crypto,
    Pack,
    Queued,
    Reads,
    Sign,
    Storage,
    Upload,
    View,
    Write,
    check_citations,
    citations_json,
    conflict,
    fetch_all,
    fetch_one,
    invalid,
    not_found,
    report_file,
    seal,
    served_version,
    unseal,
)
from app.features.after_serving.router import router
from app.features.after_serving.routes_deadlines import add_deadline
from app.features.after_serving.words import long_date, word

ANSWERS_KIND = "written_question_answers"
_CITE = re.compile(r"\[\[cite:(\d{1,3})\]\]")
_ROUND = (
    "id, number, from_party_id, received_on, document_id, deadline_id, answers_version_id, status, extracted_at,"
    " version, created_at"
)
_QUESTION = (
    "id, round_id, number, para_ref, rule_ref, text_verbatim, status, suggested_classification,"
    " classification_reason, classification, suggested_paragraph_ids, paragraph_ids, answer_text, citation_refs,"
    " told_instructing_party_at, version"
)


# --- models ---------------------------------------------------------------------------------------------


class NewRound(BaseModel):
    received_on: date
    document_id: uuid.UUID
    answers_due_on: date
    from_party_id: uuid.UUID | None = None


class RoundOut(BaseModel):
    id: uuid.UUID
    number: int
    from_party_id: uuid.UUID | None
    received_on: date
    document_id: uuid.UUID | None
    deadline_id: uuid.UUID | None
    answers_version_id: uuid.UUID | None
    status: str
    extracted_at: datetime | None
    version: int


class Rounds(BaseModel):
    rounds: list[RoundOut]


class ImportIn(BaseModel):
    round_id: uuid.UUID


class WrittenQuestionOut(BaseModel):
    id: uuid.UUID
    round_id: uuid.UUID
    number: int
    para_ref: str | None
    rule_ref: str | None
    text_verbatim: str
    status: str
    suggested_classification: str | None
    classification_reason: str | None = Field(description="A short label from the suggestion, never prose.")
    classification: str | None
    suggested_paragraph_ids: list[uuid.UUID]
    paragraph_ids: list[uuid.UUID]
    answer_text: str | None
    citation_refs: list[Citation]
    told_instructing_party_at: datetime | None
    version: int


class WrittenQuestions(BaseModel):
    questions: list[WrittenQuestionOut]


class QuestionChange(BaseModel):
    classification: str | None = Field(default=None, max_length=40)
    paragraph_ids: list[uuid.UUID] | None = Field(default=None, max_length=200)
    answer_text: str | None = Field(default=None, max_length=50000)
    citation_refs: list[Citation] | None = Field(default=None, max_length=200)


class Note(BaseModel):
    key: str
    params: dict[str, int | str | None]


class SignAnswers(BaseModel):
    round_id: uuid.UUID
    declaration_confirmed: bool


class AnswersSignedVersion(BaseModel):
    id: uuid.UUID
    version_no: int
    type: str
    display_name: str
    status: str
    sha256: str | None
    prev_version_id: uuid.UUID | None
    locked_at: datetime | None


class Signed(BaseModel):
    version: AnswersSignedVersion


# --- rounds ---------------------------------------------------------------------------------------------


async def _round(conn: Any, case_id: uuid.UUID, round_id: uuid.UUID) -> dict[str, Any] | None:
    return await fetch_one(
        conn, f"select {_ROUND} from app.written_question_rounds where id = %s and case_id = %s", (round_id, case_id)
    )


@router.post("/cases/{case_id}/written-question-rounds", status_code=201, response_model=RoundOut)
async def open_round(
    case_id: uuid.UUID, body: NewRound, access: Upload, conn: DbConn, pack: Pack, response: Response
) -> RoundOut:
    if (
        await fetch_one(conn, "select 1 from app.documents where id = %s and case_id = %s", (body.document_id, case_id))
        is None
    ):
        raise invalid("document_id")
    if (
        body.from_party_id is not None
        and await fetch_one(
            conn, "select 1 from app.case_parties where id = %s and case_id = %s", (body.from_party_id, case_id)
        )
        is None
    ):
        raise invalid("from_party_id")
    round_id = uuid.uuid4()
    deadline = await add_deadline(
        conn,
        case_id,
        kind=ANSWERS_KIND,
        due_on=body.answers_due_on,
        timezone=pack.timezone,
        created_from="round",
        reason="received",
        source_id=round_id,
    )
    row = await fetch_one(
        conn,
        "insert into app.written_question_rounds (id, case_id, number, from_party_id, received_on, document_id,"
        " deadline_id) values (%s, %s, (select coalesce(max(number), 0) + 1 from app.written_question_rounds"
        f" where case_id = %s), %s, %s, %s, %s) returning {_ROUND}",
        (round_id, case_id, case_id, body.from_party_id, body.received_on, body.document_id, deadline["id"]),
    )
    assert row is not None
    await audit.record(conn, action="written_questions.received", target=f"round:{round_id}", case_id=case_id)
    response.headers["etag"] = etag(row["version"])
    return RoundOut(**row)


@router.get("/cases/{case_id}/written-question-rounds", response_model=Rounds)
async def list_rounds(case_id: uuid.UUID, access: View, conn: DbConn) -> Rounds:
    rows = await fetch_all(
        conn, f"select {_ROUND} from app.written_question_rounds where case_id = %s order by number", (case_id,)
    )
    return Rounds(rounds=[RoundOut(**r) for r in rows])


@router.post("/cases/{case_id}/written-questions/import", status_code=202, response_model=Queued)
async def import_questions(case_id: uuid.UUID, body: ImportIn, access: Upload, conn: DbConn) -> Queued:
    found = await _round(conn, case_id, body.round_id)
    if found is None:
        raise invalid("round_id")
    if found["status"] != "open" or found["extracted_at"] is not None:
        raise conflict("already_read", "These questions have already been read.")
    return Queued(job_id=await registry.enqueue(conn, case_id, "extract_written_questions"))


# --- questions ------------------------------------------------------------------------------------------


async def question_out(conn: Any, crypto: Any, case_id: uuid.UUID, row: dict[str, Any]) -> WrittenQuestionOut:
    values = dict(row)
    values["text_verbatim"] = await unseal(conn, crypto, case_id, row["text_verbatim"], QUESTION) or ""
    values["answer_text"] = await unseal(conn, crypto, case_id, row["answer_text"], ANSWER)
    return WrittenQuestionOut(**values)


@router.get("/cases/{case_id}/written-questions", response_model=WrittenQuestions)
async def list_questions(
    case_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto, round_id: uuid.UUID | None = None
) -> WrittenQuestions:
    rows = await fetch_all(
        conn,
        f"select {', '.join('q.' + c.strip() for c in _QUESTION.split(','))} from app.written_questions q"
        " join app.written_question_rounds r on r.id = q.round_id"
        " where q.case_id = %s and (%s::uuid is null or q.round_id = %s) order by r.number, q.number",
        (case_id, round_id, round_id),
    )
    return WrittenQuestions(questions=[await question_out(conn, crypto, case_id, r) for r in rows])


async def _question(conn: Any, case_id: uuid.UUID, question_id: uuid.UUID) -> dict[str, Any]:
    row = await fetch_one(
        conn, f"select {_QUESTION} from app.written_questions where id = %s and case_id = %s", (question_id, case_id)
    )
    if row is None:
        raise not_found()
    return row


@router.patch("/cases/{case_id}/written-questions/{question_id}", response_model=WrittenQuestionOut)
async def change_question(
    case_id: uuid.UUID,
    question_id: uuid.UUID,
    body: QuestionChange,
    access: Write,
    if_match: IfMatch,
    conn: DbConn,
    crypto: Crypto,
    reads: Reads,
    pack: Pack,
    response: Response,
) -> WrittenQuestionOut:
    row = await _question(conn, case_id, question_id)
    if row["version"] != if_match:
        raise stale()
    if row["status"] in ("answered", "served"):
        raise conflict("locked", "These answers are signed and do not change.")
    if body.classification is not None and body.classification not in pack.written_question_classifications():
        raise invalid("classification")
    if body.paragraph_ids is not None:
        served = await served_version(conn, case_id)
        known = {
            r["id"]
            for r in await fetch_all(
                conn,
                "select id from app.report_paragraphs where report_version_id = %s and case_id = %s",
                (served, case_id),
            )
        }
        if not set(body.paragraph_ids) <= known:
            raise invalid("paragraph_ids")
    if body.citation_refs is not None:
        await check_citations(conn, case_id, reads, body.citation_refs)
    sets: list[str] = []
    params: list[Any] = []
    if body.classification is not None:
        sets.append("classification = %s")
        params.append(body.classification)
    if body.paragraph_ids is not None:
        sets.append("paragraph_ids = %s")
        params.append(body.paragraph_ids)
    if body.citation_refs is not None:
        sets.append("citation_refs = %s::jsonb")
        params.append(_json(citations_json(body.citation_refs)))
    if body.answer_text is not None:
        sets.append("answer_text = %s")
        params.append(await seal(conn, crypto, case_id, body.answer_text, ANSWER))
        sets.append("status = case when status = 'received' then 'answering' else status end")
    if sets:
        updated = await fetch_one(
            conn,
            f"update app.written_questions set {', '.join(sets)} where id = %s returning {_QUESTION}",
            (*params, question_id),
        )
        assert updated is not None
        row = updated
    response.headers["etag"] = etag(row["version"])
    return await question_out(conn, crypto, case_id, row)


def _json(value: Any) -> str:
    import json

    return json.dumps(value)


@router.post("/cases/{case_id}/written-questions/{question_id}/tell-instructing-party", response_model=Note)
async def tell_instructing_party(case_id: uuid.UUID, question_id: uuid.UUID, access: Write, conn: DbConn) -> Note:
    """A note for the instructing party that a question may go beyond clarifying the report. Keys and
    numbers only: the screen writes it out, with no words of the question or the records."""
    row = await _question(conn, case_id, question_id)
    found = await _round(conn, case_id, row["round_id"])
    assert found is not None
    if row["told_instructing_party_at"] is None:
        await conn.execute(
            "update app.written_questions set told_instructing_party_at = now() where id = %s", (question_id,)
        )
        await audit.record(
            conn, action="written_question.told", target=f"written_question:{question_id}", case_id=case_id
        )
    return Note(
        key="after_serving.questions.tell_note",
        params={"round": found["number"], "number": row["number"], "para_ref": row["para_ref"]},
    )


# --- signing the answers --------------------------------------------------------------------------------


async def _pages(conn: Any, case_id: uuid.UUID, page_ids: list[uuid.UUID]) -> dict[uuid.UUID, int]:
    rows = await fetch_all(
        conn, "select id, bundle_page_no from app.pages where case_id = %s and id = any(%s)", (case_id, page_ids)
    )
    return {r["id"]: int(r["bundle_page_no"]) for r in rows}


def _with_references(text: str, refs: list[dict[str, Any]], pages: dict[uuid.UUID, int], language: str) -> list[str]:
    """The answer with `[[cite:n]]` written as `[n]`, then each reference with its page number."""
    lines = [_CITE.sub(lambda m: f"[{m.group(1)}]", text)]
    for n, ref in enumerate(refs, start=1):
        page = pages.get(uuid.UUID(str(ref["page_id"])))
        if page is not None:
            lines.append(word(language, "reference", n=n, page=page))
    return lines


async def next_version_no(conn: Any, case_id: uuid.UUID) -> int:
    cur = await conn.execute(
        "select coalesce(max(version_no), 0) + 1 from app.report_versions where case_id = %s", (case_id,)
    )
    row = await cur.fetchone()
    return int(row[0]) if row else 1


async def served_details(conn: Any, case_id: uuid.UUID, version_id: uuid.UUID) -> tuple[str, date | None]:
    row = await fetch_one(
        conn,
        "select v.display_name, (select min(s.served_on) from app.report_servings s where s.report_version_id = v.id)"
        " as served_on from app.report_versions v where v.id = %s and v.case_id = %s",
        (version_id, case_id),
    )
    assert row is not None
    return str(row["display_name"]), row["served_on"]


def court_today(timezone: str) -> date:
    return datetime.now(ZoneInfo(timezone)).date()


async def lock_version(
    conn: Any, storage: Any, crypto: Any, case_id: uuid.UUID, version_id: uuid.UUID, path: str, data: bytes
) -> dict[str, Any]:
    """Store the signed file, freeze its snapshot (while still a draft) and lock the version with its hash."""
    sha = hashlib.sha256(data).hexdigest()
    await storage.put(path, await crypto.encrypt(conn, case_id, data, purpose=path))
    await conn.execute("update app.report_versions set docx_path = %s where id = %s", (path, version_id))
    await conn.execute(
        "insert into app.report_snapshots (case_id, report_version_id, kind, content_ref, sha256)"
        " values (%s, %s, 'docx', %s, %s)",
        (case_id, version_id, path.rsplit("/", 1)[-1], sha),
    )
    row = await fetch_one(
        conn,
        "update app.report_versions set status = 'locked', sha256 = %s where id = %s"
        " returning id, version_no, type, display_name, status, sha256, prev_version_id, locked_at",
        (sha, version_id),
    )
    assert row is not None
    return row


@router.post("/cases/{case_id}/written-questions/answers/sign", response_model=Signed)
async def sign_answers(
    case_id: uuid.UUID, body: SignAnswers, access: Sign, conn: DbConn, crypto: Crypto, storage: Storage, pack: Pack
) -> Signed:
    if not body.declaration_confirmed:
        raise invalid("declaration_confirmed")
    found = await _round(conn, case_id, body.round_id)
    if found is None:
        raise invalid("round_id")
    if found["status"] != "open":
        raise conflict("already_signed", "These answers are already signed.")
    rows = await fetch_all(
        conn,
        f"select {_QUESTION} from app.written_questions where round_id = %s and case_id = %s order by number",
        (body.round_id, case_id),
    )
    if not rows or any(r["answer_text"] is None for r in rows):
        raise conflict("answers_missing", "Answer every question before signing.")
    served = await served_version(conn, case_id)
    if served is None:
        raise conflict("not_served", "The report has not been served yet.")
    lang = pack.language
    name, served_on = await served_details(conn, case_id, served)
    blocks: list[docs.Block] = [("title", word(lang, "answers", n=found["number"]))]
    if served_on is not None:
        blocks.append(("body", word(lang, "answers_of", name=name, date=long_date(lang, pack.date_order, served_on))))
    page_ids = [uuid.UUID(str(c["page_id"])) for r in rows for c in r["citation_refs"]]
    pages = await _pages(conn, case_id, page_ids)
    for r in rows:
        q = await question_out(conn, crypto, case_id, r)
        heading = (
            word(lang, "question_ref", n=q.number, ref=q.para_ref) if q.para_ref else word(lang, "question", n=q.number)
        )
        blocks.append(("heading", heading))
        blocks.append(("body", q.text_verbatim))
        blocks.append(("body", word(lang, "answer")))
        blocks.extend(("body", line) for line in _with_references(q.answer_text or "", r["citation_refs"], pages, lang))
    blocks.extend(docs.declaration_blocks(pack.declarations(), word(lang, "declaration")))
    blocks.append(("body", word(lang, "dated", date=long_date(lang, pack.date_order, court_today(pack.timezone)))))
    version_id = uuid.uuid4()
    path = report_file(case_id, version_id, "answers.docx")
    await conn.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name, prev_version_id)"
        " values (%s, %s, %s, 'answers', %s, %s)",
        (version_id, case_id, await next_version_no(conn, case_id), word(lang, "answers", n=found["number"]), served),
    )
    version = await lock_version(conn, storage, crypto, case_id, version_id, path, docs.build(blocks))
    await conn.execute(
        "update app.written_questions set status = 'answered' where round_id = %s and status in ('received',"
        " 'answering')",
        (body.round_id,),
    )
    await conn.execute(
        "update app.written_question_rounds set status = 'answered', answers_version_id = %s where id = %s",
        (version_id, body.round_id),
    )
    if found["deadline_id"] is not None:
        await conn.execute(
            "update app.deadlines set done_at = now() where id = %s and done_at is null", (found["deadline_id"],)
        )
    await audit.record(conn, action="written_questions.signed", target=f"report_version:{version_id}", case_id=case_id)
    return Signed(version=AnswersSignedVersion(**version))
