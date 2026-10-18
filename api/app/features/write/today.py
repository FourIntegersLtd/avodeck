"""Slice E's Today items (screen 03, `core/contracts/today.py`): keys and counts only (rule 6).

- `write_questions_without_opinion`: per case where the person is the named expert, the confirmed
  questions with no saved opinion yet. Opens screen 15.
- `write_points_not_considered`: per case where the person is the named expert, the points still
  suggested or gone stale, waiting for the expert to accept or remove them. Opens screen 14.
Only the named expert writes (`opinion.write`), so no one else sees them.
"""

from __future__ import annotations

from psycopg import AsyncConnection

from app.core.contracts.today import CaseCard, NeedsYouItem, needs_you
from app.features.write.common import ANSWERABLE

_WITHOUT_OPINION = """
select q.case_id, count(*)::int
from app.questions q
join app.cases c on c.id = q.case_id
where q.case_id = any(%s) and c.named_expert_id = app.current_person_id() and q.status = any(%s)
  and not exists (select 1 from app.opinions o where o.question_id = q.id)
group by q.case_id
"""

_NOT_CONSIDERED = """
select p.case_id, count(*)::int
from app.points p
join app.cases c on c.id = p.case_id
where p.case_id = any(%s) and c.named_expert_id = app.current_person_id() and p.status in ('suggested', 'stale')
group by p.case_id
"""


async def _items(
    conn: AsyncConnection, sql: str, args: tuple[object, ...], kind: str, what: str, screen: str, rank: int
) -> list[NeedsYouItem]:
    cur = await conn.execute(sql, args)
    return [
        NeedsYouItem(
            kind=kind,
            case_id=case_id,
            rank=rank,
            label_key=f"write.today.{what}",
            action_key=f"write.today.{what}_action",
            screen=screen,
            count=count,
        )
        for case_id, count in await cur.fetchall()
    ]


@needs_you.provider("write_questions_without_opinion")
async def questions_without_opinion(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    args = ([c.case_id for c in cards], list(ANSWERABLE))
    kind = "write_questions_without_opinion"
    return await _items(conn, _WITHOUT_OPINION, args, kind, "questions_without_opinion", "S15", 60)


@needs_you.provider("write_points_not_considered")
async def points_not_considered(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    args = ([c.case_id for c in cards],)
    kind = "write_points_not_considered"
    return await _items(conn, _NOT_CONSIDERED, args, kind, "points_not_considered", "S14", 55)
