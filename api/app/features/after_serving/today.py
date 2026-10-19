"""Slice G's Today items (screen 03). Only the named expert answers questions, reads for the report and
confirms claims, so only they see these. Keys and counts only (rule 6)."""

from __future__ import annotations

import uuid
from typing import Any

from psycopg import AsyncConnection

from app.core.contracts.today import CaseCard, NeedsYouItem, needs_you
from app.features.after_serving import events
from app.features.after_serving.common import due_at, fetch_all, person_id


async def _mine(conn: AsyncConnection, cards: list[CaseCard]) -> list[uuid.UUID]:
    """The cases on the list where the signed-in person is the named expert."""
    if not cards:
        return []
    rows = await fetch_all(
        conn,
        "select id from app.cases where id = any(%s) and named_expert_id = app.current_person_id()",
        ([c.case_id for c in cards],),
    )
    return [r["id"] for r in rows]


@needs_you.provider("after_serving_written_questions_due")
async def written_questions_due(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cases = await _mine(conn, cards)
    if not cases:
        return []
    rows = await fetch_all(
        conn,
        "select r.case_id, r.id, count(q.id) as n, d.due_on, d.timezone from app.written_question_rounds r"
        " join app.written_questions q on q.round_id = r.id and q.status in ('received', 'answering')"
        " left join app.deadlines d on d.id = r.deadline_id"
        " where r.case_id = any(%s) and r.status = 'open' group by r.case_id, r.id, d.due_on, d.timezone"
        " order by d.due_on nulls last",
        (cases,),
    )
    return [
        NeedsYouItem(
            kind="after_serving_written_questions_due",
            case_id=r["case_id"],
            rank=20,
            due_at=due_at(r["due_on"], r["timezone"]) if r["due_on"] else None,
            label_key="after_serving.today.questions_due",
            action_key="after_serving.today.answer_questions",
            screen="S20",
            count=int(r["n"]),
        )
        for r in rows
    ]


@needs_you.provider("after_serving_new_pages")
async def new_pages(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cases = await _mine(conn, cards)
    app = events.current_app()
    reads: Any = getattr(app.state, "page_reads", None) if app is not None else None
    me = await person_id(conn)
    out: list[NeedsYouItem] = []
    for case_id in cases:
        cur = await conn.execute("select app.after_serving_served_at(%s)", (case_id,))
        row = await cur.fetchone()
        if row is None or row[0] is None:
            continue
        pages = await fetch_all(
            conn,
            "select id from app.pages where case_id = %s and created_at > %s and bundle_page_no is not null",
            (case_id, row[0]),
        )
        if not pages:
            continue
        read = set(await reads.read_page_ids(conn, case_id, me)) if reads is not None and me is not None else set()
        unread = [p for p in pages if p["id"] not in read]
        if unread:
            out.append(
                NeedsYouItem(
                    kind="after_serving_new_pages",
                    case_id=case_id,
                    rank=30,
                    label_key="after_serving.today.new_pages",
                    action_key="after_serving.today.read_new_pages",
                    screen="S21",
                    count=len(unread),
                )
            )
    return out


@needs_you.provider("after_serving_opposing_to_check")
async def opposing_to_check(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
    cases = await _mine(conn, cards)
    if not cases:
        return []
    rows = await fetch_all(
        conn,
        "select case_id, count(*) as n from app.opposing_claims where case_id = any(%s) and status <> 'unchecked'"
        " and not expert_confirmed group by case_id",
        (cases,),
    )
    return [
        NeedsYouItem(
            kind="after_serving_opposing_to_check",
            case_id=r["case_id"],
            rank=40,
            label_key="after_serving.today.opposing_to_check",
            action_key="after_serving.today.check_opposing",
            screen="S21",
            count=int(r["n"]),
        )
        for r in rows
    ]
