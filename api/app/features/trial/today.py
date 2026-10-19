"""Slice I's Today items (screen 03, `core/contracts/today.py`): keys, counts and dates only (rule 6).

- `trial_joint_statement_to_sign`: per meeting not yet signed where the person is the named expert, due at
  the end of its sign-by day. Opens screen 22.
- `trial_pages_to_confirm`: per case where the person is the named expert, the pages in the current court
  bundle whose trial number waits for the expert. Opens screen 23.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

from psycopg import AsyncConnection

from app.core.contracts.today import CaseCard, NeedsYouItem, needs_you

_TO_SIGN = """
select m.case_id, m.sign_by
from app.expert_meetings m
join app.cases c on c.id = m.case_id
where m.case_id = any(%s) and c.named_expert_id = app.current_person_id()
  and m.status in ('draft', 'out_for_signature')
order by m.sign_by
"""

_TO_CONFIRM = """
select k.case_id, count(*)::int
from app.concordance k
join app.court_bundles b on b.id = k.court_bundle_id
join app.cases c on c.id = k.case_id
where k.case_id = any(%s) and c.named_expert_id = app.current_person_id()
  and b.status = 'matched' and k.trial_page_no is not null and not k.confirmed
group by k.case_id
"""


@needs_you.provider("trial_joint_statement_to_sign")
async def statements_to_sign(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cur = await conn.execute(_TO_SIGN, ([c.case_id for c in cards],))
    return [
        NeedsYouItem(
            kind="trial_joint_statement_to_sign",
            case_id=case_id,
            rank=30,
            due_at=datetime.combine(sign_by, time(23, 59, 59), tzinfo=UTC),
            label_key="trial.today.joint_statement_to_sign",
            action_key="trial.today.joint_statement_to_sign_action",
            screen="S22",
        )
        for case_id, sign_by in await cur.fetchall()
    ]


@needs_you.provider("trial_pages_to_confirm")
async def pages_to_confirm(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cur = await conn.execute(_TO_CONFIRM, ([c.case_id for c in cards],))
    return [
        NeedsYouItem(
            kind="trial_pages_to_confirm",
            case_id=case_id,
            rank=35,
            label_key="trial.today.pages_to_confirm",
            action_key="trial.today.pages_to_confirm_action",
            screen="S23",
            count=count,
        )
        for case_id, count in await cur.fetchall()
    ]
