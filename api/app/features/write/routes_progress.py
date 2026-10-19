"""Where the expert has got to on each question (screens 14 and 15).

A question's points are done once it has at least one point kept and none still suggested or stale;
it has an opinion once one is saved. Only flags and counts leave the server (rule 6).
"""

from __future__ import annotations

import uuid

from pydantic import BaseModel

from app.core.http import DbConn
from app.features.write.common import ANSWERABLE, View, short_title
from app.features.write.router import router


class QuestionProgress(BaseModel):
    question_id: uuid.UUID
    number: int
    short_title: str
    points_done: bool
    has_opinion: bool


class WriteProgress(BaseModel):
    answered: int
    total: int
    questions: list[QuestionProgress]


_SQL = """
select q.id, q.number, coalesce(q.confirmed_text, q.text_verbatim),
  exists (select 1 from app.points p where p.question_id = q.id and p.status in ('accepted'))
    and not exists (select 1 from app.points p where p.question_id = q.id and p.status in ('suggested', 'stale')),
  exists (select 1 from app.opinions o where o.question_id = q.id)
from app.questions q
where q.case_id = %s and q.status = any(%s)
order by q.number
"""


@router.get("/cases/{case_id}/write-progress", response_model=WriteProgress)
async def write_progress(case_id: uuid.UUID, access: View, conn: DbConn) -> WriteProgress:
    """Per answerable question: are its points settled, and is there an opinion; plus the answered count."""
    cur = await conn.execute(_SQL, (case_id, list(ANSWERABLE)))
    found = await cur.fetchall()
    rows = [
        QuestionProgress(
            question_id=r[0], number=r[1], short_title=short_title(r[2]), points_done=r[3], has_opinion=r[4]
        )
        for r in found
    ]
    return WriteProgress(answered=sum(r.has_opinion for r in rows), total=len(rows), questions=rows)
