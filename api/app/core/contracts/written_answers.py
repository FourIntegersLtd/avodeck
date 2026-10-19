"""Served answers to written questions, read-only (slice G writes them; slice I puts them in the trial pack
and the offline copy, S11).

Each round of questions G serves has its own answers document (`written_question_rounds.answers_version_id`,
a `report_versions` row of type answers). Answer words are sealed by slice G under `ANSWER_PURPOSE` and are
opened here: they are the expert's served words and may hold patient details, so never log them (rule 6)
and strip identifiers before any model call (rule 7). Only served rounds count. Runs on the caller's
connection, so row-level security applies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto

ANSWER_PURPOSE = "written_question_answer"


@dataclass(frozen=True)
class ServedAnswer:
    question_no: int
    text: str


@dataclass(frozen=True)
class ServedRound:
    round_no: int
    answers_version_id: uuid.UUID
    answers: tuple[ServedAnswer, ...]


async def served_answers(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> list[ServedRound]:
    """The case's served rounds in round order, each with its answered questions in question order."""
    cur = await conn.execute(
        "select r.id, r.number, r.answers_version_id, q.number, q.answer_text"
        " from app.written_question_rounds r"
        " left join app.written_questions q on q.round_id = r.id and q.answer_text is not null"
        " where r.case_id = %s and r.status = 'served' and r.answers_version_id is not null"
        " order by r.number, q.number",
        (case_id,),
    )
    rounds: dict[uuid.UUID, tuple[int, uuid.UUID, list[ServedAnswer]]] = {}
    for round_id, round_no, version_id, question_no, sealed in await cur.fetchall():
        entry = rounds.setdefault(round_id, (int(round_no), version_id, []))
        if sealed is not None:
            text = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=ANSWER_PURPOSE)
            entry[2].append(ServedAnswer(int(question_no), text.decode()))
    return [ServedRound(no, version, tuple(answers)) for no, version, answers in rounds.values()]
