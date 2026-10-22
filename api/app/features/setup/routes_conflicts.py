"""Screen 05: before you start, and withdrawing from a case (architecture 5.2, slice C brief, scenario S16).

The questions and the paragraph their answers produce come from the jurisdiction pack's `conflicts.yaml`
(rule 11): every answer "no" gives `statement.none`; any "yes" gives `statement.disclosed` followed by the
expert's own details. Details and the withdrawal reason are encrypted under the case key. The coverage
check asks the database whether the named expert's roles, in the settings the specialty counts, cover the
case period; the roles themselves are never shown to anyone else. Only the named expert answers,
declares and withdraws (rule 9); withdrawing needs step-up (rule 10).

Deadlines belong to the slices that set them: withdrawing publishes `CaseWithdrawn` in this transaction,
and each slice that owns deadlines cancels the case's open ones and says how many.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import Depends, Request
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.contracts import events
from app.core.http import DbConn
from app.core.packs import MergedPack
from app.features.setup.common import CODE, invalid
from app.features.setup.notes import NoteDraft, NoteItem, draft
from app.features.setup.router import router
from app.features.setup.routes_case import Crypto, ViewContent, _pack, conflict

Declare = Annotated[CaseAccess, Depends(require_case_access("conflict.declare"))]
Withdraw = Annotated[CaseAccess, Depends(require_case_access("case.withdraw"))]

_DETAIL = "conflict_detail"
_REASON = "withdrawal_reason"
_WORD = re.compile(r"\{([a-z_]+)\}")
_ENDED = ("withdrawn", "archived", "deleted")


class ConflictQuestion(BaseModel):
    key: str
    text: str = Field(description="The pack's question, with its words filled in.")
    detail_required: bool
    answer: Literal["yes", "no"] | None
    detail: str | None


class Coverage(BaseModel):
    status: Literal["covered", "partial", "none", "unknown"]
    period_start: str | None
    period_end: str | None


class OtherCases(BaseModel):
    checked: int = Field(description="Your other cases compared by the patient's identifier; 0 if none yet.")
    matches: int = Field(description="How many of them are about the same patient. Never named here.")
    organisations: list[str] = Field(description="Where the checked cases sit, by organisation name.")


class Conflicts(BaseModel):
    questions: list[ConflictQuestion]
    complete: bool
    paragraph: str | None = Field(description="The conflicts paragraph of the report, once every question is answered.")
    coverage: Coverage
    other_cases: OtherCases
    coverage_question: str | None = Field(default=None, description="The specialty's role-coverage question.")
    warnings: list[str] = Field(description="i18n keys.")
    declared_at: datetime | None
    outcome: Literal["none", "disclosed"] | None
    changed_since_declared: bool = Field(
        default=False, description="An answer changed after the latest declaration; declare again."
    )


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=CODE)
    answer: Literal["yes", "no"]
    detail: str | None = Field(default=None, min_length=1, max_length=4000)


class AnswersIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answers: list[Answer] = Field(min_length=1, max_length=40)


def _fill(pack: MergedPack, text: str) -> str:
    def word(m: re.Match[str]) -> str:
        value = pack.vocabulary.get(m.group(1))
        return value if isinstance(value, str) else m.group(0)

    return _WORD.sub(word, text)


async def _answers(conn: DbConn, crypto: Any, case_id: uuid.UUID) -> dict[str, tuple[str, str | None]]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select question_key, answer, detail from app.conflict_answers where case_id = %s", (case_id,)
        )
        rows = await cur.fetchall()
    out: dict[str, tuple[str, str | None]] = {}
    for r in rows:
        detail = None
        if r["detail"] is not None:
            detail = (await crypto.decrypt(conn, case_id, bytes(r["detail"]), purpose=_DETAIL)).decode()
        out[r["question_key"]] = (r["answer"], detail)
    return out


def _paragraph(pack: MergedPack, questions: list[ConflictQuestion]) -> str | None:
    if any(q.answer is None for q in questions):
        return None
    statement = pack.conflicts()["statement"]
    details = [q.detail for q in questions if q.answer == "yes" and q.detail]
    if all(q.answer == "no" for q in questions):
        return str(statement["none"])
    return "\n".join([str(statement["disclosed"]), *details])


async def _coverage(conn: DbConn, pack: MergedPack, case_id: uuid.UUID) -> Coverage:
    settings = [r["key"] for r in pack.coverage_roles()]
    cur = await conn.execute(
        "select app.expert_covers_period(%s, %s), period_start::text, period_end::text from app.cases where id = %s",
        (case_id, settings, case_id),
    )
    row = await cur.fetchone()
    assert row is not None
    return Coverage(status=row[0] or "unknown", period_start=row[1], period_end=row[2])


async def _other_cases(conn: DbConn, case_id: uuid.UUID) -> OtherCases:
    cur = await conn.execute(
        "select o.name, k.key_hash = mine.key_hash from app.case_subject_keys mine"
        " join app.case_subject_keys k on k.case_id <> mine.case_id"
        " join app.cases c on c.id = k.case_id join app.organisations o on o.id = c.organisation_id"
        " where mine.case_id = %s and c.named_expert_id = app.current_person_id() and c.status <> 'deleted'"
        " order by o.name",
        (case_id,),
    )
    rows = await cur.fetchall()
    return OtherCases(
        checked=len(rows), matches=sum(1 for r in rows if r[1]), organisations=sorted({str(r[0]) for r in rows})
    )


async def _state(request: Request, conn: DbConn, crypto: Any, case_id: uuid.UUID) -> Conflicts:
    pack = await _pack(request, conn, case_id)
    given = await _answers(conn, crypto, case_id)
    questions = [
        ConflictQuestion(
            key=q["key"],
            text=_fill(pack, str(q["text"])),
            detail_required=bool(q.get("detail_required", False)),
            answer=given[q["key"]][0] if q["key"] in given else None,  # type: ignore[arg-type]
            detail=given[q["key"]][1] if q["key"] in given else None,
        )
        for q in pack.conflicts()["questions"]
    ]
    coverage = await _coverage(conn, pack, case_id)
    warnings = [f"setup.conflicts.warning.coverage_{coverage.status}"] if coverage.status in ("none", "partial") else []
    cur = await conn.execute(
        "select d.created_at, d.outcome, exists (select 1 from app.conflict_answer_history h"
        " where h.case_id = d.case_id and h.recorded_at > d.created_at)"
        " from app.conflict_declarations d where d.case_id = %s order by d.created_at desc limit 1",
        (case_id,),
    )
    declared = await cur.fetchone()
    changed = bool(declared and declared[2])
    if changed:
        warnings.append("setup.conflicts.warning.declare_again")
    return Conflicts(
        questions=questions,
        complete=all(q.answer is not None for q in questions),
        paragraph=_paragraph(pack, questions),
        coverage=coverage,
        other_cases=await _other_cases(conn, case_id),
        coverage_question=_fill(pack, str(pack.vocabulary["coverage_question"]))
        if isinstance(pack.vocabulary.get("coverage_question"), str)
        else None,
        warnings=warnings,
        declared_at=declared[0] if declared else None,
        outcome=declared[1] if declared else None,
        changed_since_declared=changed,
    )


@router.get("/cases/{case_id}/conflicts", response_model=Conflicts)
async def get_conflicts(
    case_id: uuid.UUID, access: ViewContent, request: Request, conn: DbConn, crypto: Crypto
) -> Conflicts:
    return await _state(request, conn, crypto, case_id)


@router.put("/cases/{case_id}/conflicts", response_model=Conflicts)
async def put_conflicts(
    case_id: uuid.UUID, body: AnswersIn, access: Declare, request: Request, conn: DbConn, crypto: Crypto
) -> Conflicts:
    pack = await _pack(request, conn, case_id)
    asked = {q["key"]: bool(q.get("detail_required", False)) for q in pack.conflicts()["questions"]}
    for i, a in enumerate(body.answers):
        if a.key not in asked:
            raise invalid(f"answers.{i}.key")
        if a.answer == "no" and a.detail is not None:
            raise invalid(f"answers.{i}.detail")
        if a.answer == "yes" and asked[a.key] and not (a.detail and a.detail.strip()):
            raise invalid(f"answers.{i}.detail")
    given = await _answers(conn, crypto, case_id)
    for a in body.answers:
        if given.get(a.key) == (a.answer, a.detail):
            continue  # the same answer again: nothing changes, so no history row
        detail = None if a.detail is None else await crypto.encrypt(conn, case_id, a.detail.encode(), purpose=_DETAIL)
        await conn.execute(
            "insert into app.conflict_answers (case_id, question_key, answer, detail) values (%s, %s, %s, %s)"
            " on conflict (case_id, question_key) do update set answer = excluded.answer, detail = excluded.detail,"
            " answered_by = app.current_person_id()",
            (case_id, a.key, a.answer, detail),
        )
    await audit.record(conn, action="case.conflicts_answered", target=f"case:{case_id}", case_id=case_id)
    return await _state(request, conn, crypto, case_id)


class Declared(BaseModel):
    declared_at: datetime
    outcome: Literal["none", "disclosed"]
    note: NoteDraft


@router.post("/cases/{case_id}/conflicts/declare", response_model=Declared)
async def declare(case_id: uuid.UUID, access: Declare, request: Request, conn: DbConn, crypto: Crypto) -> Declared:
    state = await _state(request, conn, crypto, case_id)
    if not state.complete:
        raise conflict("Answer every question first.")
    outcome: Literal["none", "disclosed"] = "disclosed" if any(q.answer == "yes" for q in state.questions) else "none"
    cur = await conn.execute(
        "insert into app.conflict_declarations (case_id, outcome) values (%s, %s) returning created_at",
        (case_id, outcome),
    )
    row = await cur.fetchone()
    assert row is not None
    await audit.record(conn, action="case.conflicts_declared", target=f"case:{case_id}", case_id=case_id)
    items = [NoteItem(text=q.detail, label=q.text) for q in state.questions if q.answer == "yes" and q.detail]
    return Declared(declared_at=row[0], outcome=outcome, note=await draft(conn, case_id, "conflict_declared", items))


class WithdrawIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=4000)


class Withdrawn(BaseModel):
    status: Literal["withdrawn"]
    deadlines_cancelled: int = Field(
        description="Open deadlines cancelled by the slices that own them (CaseWithdrawn)."
    )
    note: NoteDraft


@router.post("/cases/{case_id}/withdraw", response_model=Withdrawn)
async def withdraw(case_id: uuid.UUID, body: WithdrawIn, access: Withdraw, conn: DbConn, crypto: Crypto) -> Withdrawn:
    if not body.reason.strip():
        raise invalid("reason")
    cur = await conn.execute("select status from app.cases where id = %s for update", (case_id,))
    row = await cur.fetchone()
    assert row is not None
    if row[0] in _ENDED:
        raise conflict("This case has already ended.")
    reason = await crypto.encrypt(conn, case_id, body.reason.encode(), purpose=_REASON)
    await conn.execute("insert into app.case_withdrawals (case_id, reason) values (%s, %s)", (case_id, reason))
    await conn.execute("update app.cases set status = 'withdrawn' where id = %s", (case_id,))
    await audit.record(conn, action="case.withdrawn", target=f"case:{case_id}", case_id=case_id)
    withdrawn = events.CaseWithdrawn(case_id)
    await events.events.publish(conn, withdrawn)
    cancelled = sum(withdrawn.cancelled)
    return Withdrawn(
        status="withdrawn", deadlines_cancelled=cancelled, note=await draft(conn, case_id, "withdrawal", [])
    )
