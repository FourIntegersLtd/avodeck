"""Slice C's Today items (screen 03, `core/contracts/today.py`): keys and counts only (rule 6).

- `setup_questions_to_confirm`: per case where the person is the named expert and the latest letter's
  questions are waiting (suggested, set not locked). Opens screen 06.
- `setup_profile_incomplete`: once, on the first case shown where the person is the named expert, while
  the profile lacks a name, profession, registration body or number, or a specialty. Opens screen 02.
"""

from __future__ import annotations

from psycopg import AsyncConnection

from app.core.contracts.today import CaseCard, NeedsYouItem, needs_you

_WAITING = """
select q.case_id, count(*)::int
from app.questions q
join app.cases c on c.id = q.case_id
join app.instruction_versions v on v.id = q.instruction_version_id
where q.case_id = any(%s) and c.named_expert_id = app.current_person_id()
  and q.status = 'suggested' and v.locked_at is null
group by q.case_id
"""

_MISSING = """
select (p.full_name is null or btrim(p.full_name) = '')::int
     + (e.profession is null)::int
     + (e.registration_body is null or btrim(e.registration_body) = '')::int
     + (e.registration_number is null or btrim(e.registration_number) = '')::int
     + (coalesce(cardinality(e.specialties), 0) = 0)::int
from app.people p left join app.experts e on e.person_id = p.id
where p.id = app.current_person_id()
"""


@needs_you.provider("setup_questions_to_confirm")
async def questions_to_confirm(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cur = await conn.execute(_WAITING, ([c.case_id for c in cards],))
    return [
        NeedsYouItem(
            kind="setup_questions_to_confirm",
            case_id=case_id,
            rank=20,
            label_key="setup.today.questions_to_confirm",
            action_key="setup.today.questions_to_confirm_action",
            screen="S06",
            count=count,
        )
        for case_id, count in await cur.fetchall()
    ]


@needs_you.provider("setup_profile_incomplete")
async def profile_incomplete(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cur = await conn.execute(
        "select id from app.cases where id = any(%s) and named_expert_id = app.current_person_id()",
        ([c.case_id for c in cards],),
    )
    mine = {r[0] for r in await cur.fetchall()}
    first = next((c.case_id for c in cards if c.case_id in mine), None)
    if first is None:
        return []
    cur = await conn.execute(_MISSING)
    row = await cur.fetchone()
    missing = int(row[0]) if row else 0
    if not missing:
        return []
    return [
        NeedsYouItem(
            kind="setup_profile_incomplete",
            case_id=first,
            rank=40,
            label_key="setup.today.profile_incomplete",
            action_key="setup.today.profile_incomplete_action",
            screen="S02",
            count=missing,
        )
    ]
