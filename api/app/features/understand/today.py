"""Slice D's Today item (screen 03, `core/contracts/today.py`): keys and counts only (rule 6).

- `understand_things_to_look_at`: per case where the person is the named expert and open findings wait
  for a decision, with how many. Opens screen 13.
"""

from __future__ import annotations

from psycopg import AsyncConnection

from app.core.contracts.today import CaseCard, NeedsYouItem, needs_you

_UNDECIDED = """
select f.case_id, count(*)::int
from app.findings f
join app.cases c on c.id = f.case_id
where f.case_id = any(%s) and c.named_expert_id = app.current_person_id()
  and f.status = 'open' and f.decision is null
group by f.case_id
"""


@needs_you.provider("understand_things_to_look_at")
async def things_to_look_at(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cur = await conn.execute(_UNDECIDED, ([c.case_id for c in cards],))
    return [
        NeedsYouItem(
            kind="understand_things_to_look_at",
            case_id=case_id,
            rank=30,
            label_key="understand.today.things_to_look_at",
            action_key="understand.today.things_to_look_at_action",
            screen="S13",
            count=count,
        )
        for case_id, count in await cur.fetchall()
    ]
