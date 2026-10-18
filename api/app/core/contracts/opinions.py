"""Opinions (architecture 4.6): the expert's own words on each question, written in slice E (screen 15).

Slice F builds the report from `latest_opinions()`: the current save of the opinion on each question
(the table row, which equals its newest `opinion_versions` row), opened from its seal under the case
key, with its citations as saved. F re-checks those citations with `citations.citation_problems()`.
Runs on the caller's connection, so row-level security applies: an outsider gets none.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from app.core.contracts.citations import Citation
from app.core.crypto import CaseCrypto

BODY_PURPOSE = "opinion_body"
TEST_TEXT_PURPOSE = "opinion_test_text"


class OpinionText(BaseModel):
    model_config = ConfigDict(frozen=True)

    question_id: uuid.UUID
    opinion_id: uuid.UUID
    version: int
    body: str
    test_text: str | None = None
    view: str | None = None
    provisional: bool = False
    citations: list[Citation]


async def latest_opinions(conn: AsyncConnection, case_id: uuid.UUID, crypto: CaseCrypto) -> list[OpinionText]:
    """The latest opinion on every question of the case, in question order (number, then id)."""
    cur = await conn.execute(
        "select o.question_id, o.id, o.version, o.body, o.test_text, o.view, o.provisional, o.citation_refs"
        " from app.opinions o join app.questions q on q.id = o.question_id"
        " where o.case_id = %s order by q.number, q.id",
        (case_id,),
    )
    out: list[OpinionText] = []
    for question_id, opinion_id, version, body, test_text, view, provisional, refs in await cur.fetchall():
        opened = await crypto.decrypt(conn, case_id, bytes(body), purpose=BODY_PURPOSE)
        test = (
            None
            if test_text is None
            else (await crypto.decrypt(conn, case_id, bytes(test_text), purpose=TEST_TEXT_PURPOSE)).decode()
        )
        out.append(
            OpinionText(
                question_id=question_id,
                opinion_id=opinion_id,
                version=version,
                body=opened.decode(),
                test_text=test,
                view=view,
                provisional=provisional,
                citations=[Citation.model_validate(c) for c in refs],
            )
        )
    return out
