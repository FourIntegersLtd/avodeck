"""Slice E's case tables (migrations 0500-0599, architecture 4.6).

Row-level security repeats the case access check (rule 9): everyone on the case sees the rows, people
outside it and the bare API role see nothing, and only the named expert writes (`opinion.write`).
Suggested points and witness claims come only from the worker. Every save of an opinion keeps a version
that is never changed; a point or claim becomes `stale` when a fact under it changes; a lint dismissal
needs a reason and is kept. Patient words (opinion text, range notes, claims, flagged words) are stored
encrypted under the case key, so these tests write placeholder bytes.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.contracts import add_fact, add_question
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

INSERTS = {
    "points": "insert into app.points (case_id, question_id, fact_id, side, status, origin, fact_version)"
    " values (%(c)s, %(q)s, %(f)s, 'supports_claim', 'accepted', 'expert', 2)",
    "range_notes": "insert into app.range_notes (case_id, question_id, text) values (%(c)s, %(q)s, '\\x00')",
    "witness_claims": "insert into app.witness_claims (case_id, document_id, claim_index, text_verbatim, word_ids,"
    " verdict, fact_ids, fact_versions) values (%(c)s, %(d)s, 0, '\\x00', '{w1}', 'contradicts', array[%(f)s]::uuid[],"
    " '{2}')",
    "opinions": "insert into app.opinions (case_id, question_id, body) values (%(c)s, %(q)s, '\\x00')",
    "lint_issues": "insert into app.lint_issues (case_id, opinion_id, opinion_version, sentence_index, type, words,"
    " source) values (%(c)s, %(o)s, 1, 0, 'loaded_language', '\\x00', 'regex')",
    "relevant_facts": "insert into app.relevant_facts (case_id, question_id, fact_id, why_relevant, fact_version)"
    " values (%(c)s, %(q)s, %(f)s, 'advice to return', 2)",
    "witness_comparisons": "insert into app.witness_comparisons (case_id, document_id) values (%(c)s, %(d)s)",
}
SUGGEST_POINT = (
    "insert into app.points (case_id, question_id, fact_id, side, status, origin, fact_version, reason)"
    " values (%(c)s, %(q)s, %(f)s, 'neutral', 'suggested', 'ai', 2, 'dated result')"
)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, co_expert, assistant, viewer, outsider = (seed.person() for _ in range(5))
    org = seed.org(
        kind="team",
        members={expert: "expert", co_expert: "expert", assistant: "assistant", viewer: "expert"},
    )
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(
        org,
        expert,
        status="active",
        members={co_expert: "co_expert", assistant: "assistant", viewer: "viewer"},
    )
    document = seed.document(case)
    page = seed.page(case, document, 1)
    fact = add_fact(admin, case, page, status="verified")
    question = add_question(admin, case, status="confirmed")
    return {
        "expert": expert,
        "co_expert": co_expert,
        "assistant": assistant,
        "viewer": viewer,
        "outsider": outsider,
        "case": case,
        "document": document,
        "fact": fact,
        "question": question,
    }


def params(world: dict[str, uuid.UUID], **more: uuid.UUID) -> dict[str, uuid.UUID]:
    return {"c": world["case"], "q": world["question"], "f": world["fact"], "d": world["document"], **more}


def add_opinion(admin: psycopg.Connection, world: dict[str, uuid.UUID]) -> uuid.UUID:
    row = admin.execute(INSERTS["opinions"] + " returning id", params(world)).fetchone()
    assert row is not None
    return row[0]


def insert_params(admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str) -> dict[str, uuid.UUID]:
    if table == "lint_issues":
        return params(world, o=add_opinion(admin, world))
    return params(world)


async def run_as(
    api_db: Database, person: uuid.UUID, sql: str, args: Mapping[str, Any] | None = None
) -> list[tuple[object, ...]]:
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, args)
        return await cur.fetchall() if cur.description else []


@pytest.mark.parametrize("table", [*sorted(INSERTS), "opinion_versions"])
async def test_people_on_the_case_see_the_rows_and_others_do_not(
    api_db: Database, api_dsn: str, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    if table == "opinion_versions":
        add_opinion(admin, world)
    else:
        admin.execute(INSERTS[table], insert_params(admin, world, table))
    count = f"select count(*) from app.{table}"  # noqa: S608
    for person in ("expert", "co_expert", "assistant", "viewer"):
        assert await run_as(api_db, world[person], count) == [(1,)], person
    assert await run_as(api_db, world["outsider"], count) == [(0,)]
    with psycopg.connect(api_dsn) as bare:
        assert bare.execute(count).fetchone() == (0,)


@pytest.mark.parametrize("table", sorted(set(INSERTS) - {"witness_claims", "relevant_facts"}))
async def test_only_the_named_expert_writes(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    args = insert_params(admin, world, table)
    for person in ("co_expert", "assistant", "viewer", "outsider"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world[person], INSERTS[table], args)
    await run_as(api_db, world["expert"], INSERTS[table], args)


@pytest.mark.parametrize("sql", [SUGGEST_POINT, INSERTS["witness_claims"], INSERTS["relevant_facts"]])
async def test_suggested_points_and_claims_come_only_from_the_worker(
    api_db: Database, world: dict[str, uuid.UUID], sql: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], sql, params(world))


async def test_an_expert_cannot_change_another_persons_view_of_a_claim(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERTS["witness_claims"], params(world))
    for person in ("co_expert", "assistant", "viewer"):
        changed = await run_as(api_db, world[person], "update app.witness_claims set status = 'confirmed' returning id")
        assert changed == [], person
    decided = await run_as(
        api_db, world["expert"], "update app.witness_claims set status = 'confirmed' returning decided_by"
    )
    assert decided == [(world["expert"],)]


@pytest.mark.parametrize(
    "table", ["points", "witness_claims", "opinions", "range_notes", "relevant_facts", "witness_comparisons"]
)
async def test_points_claims_opinions_and_notes_are_never_deleted(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    admin.execute(INSERTS[table], params(world))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], f"delete from app.{table}")  # noqa: S608


async def test_every_save_of_an_opinion_keeps_a_version_that_never_changes(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    await run_as(api_db, world["expert"], INSERTS["opinions"], params(world))
    await run_as(api_db, world["expert"], "update app.opinions set body = '\\x01', view = 'zz_view'")
    await run_as(api_db, world["expert"], "update app.opinions set provisional = true")
    rows = await run_as(
        api_db,
        world["expert"],
        "select v.version_no, v.saved_by, v.view, v.provisional, o.version from app.opinion_versions v"
        " join app.opinions o on o.id = v.opinion_id order by v.version_no",
    )
    assert rows == [
        (1, world["expert"], None, False, 3),
        (2, world["expert"], "zz_view", False, 3),
        (3, world["expert"], "zz_view", True, 3),
    ]
    for sql in (
        "update app.opinion_versions set body = '\\x02'",
        "delete from app.opinion_versions",
        "insert into app.opinion_versions (case_id, opinion_id, version_no, body)"
        " select case_id, opinion_id, 9, body from app.opinion_versions limit 1",
    ):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world["expert"], sql)


async def test_one_opinion_and_one_range_note_per_question(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    for table in ("opinions", "range_notes"):
        admin.execute(INSERTS[table], params(world))
        with pytest.raises(psycopg.errors.UniqueViolation):
            admin.execute(INSERTS[table], params(world))


async def test_point_status_follows_the_state_machine(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(SUGGEST_POINT, params(world))
    expert = world["expert"]
    with pytest.raises(psycopg.Error) as no_reason:
        await run_as(api_db, expert, "update app.points set status = 'dismissed'")
    assert no_reason.value.sqlstate in ("AV409", "23514")
    await run_as(api_db, expert, "update app.points set status = 'dismissed', dismissed_reason = 'not relevant'")
    await run_as(api_db, expert, "update app.points set status = 'accepted', side = 'supports_clinician'")
    with pytest.raises(psycopg.Error) as back:
        await run_as(api_db, expert, "update app.points set status = 'suggested'")
    assert back.value.sqlstate == "AV409"
    with pytest.raises(psycopg.Error) as stale:
        await run_as(api_db, expert, "update app.points set status = 'stale'")
    assert stale.value.sqlstate == "AV409"
    with pytest.raises(psycopg.Error) as side:
        await run_as(api_db, expert, "update app.points set side = 'somewhere'")
    assert side.value.sqlstate == "23514"


async def test_a_point_or_claim_becomes_stale_when_its_fact_changes_until_reviewed(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERTS["points"], params(world))
    admin.execute(INSERTS["witness_claims"], params(world))
    admin.execute("update app.facts set status = 'rejected' where id = %s", (world["fact"],))
    rows = admin.execute(
        "select (select status from app.points where case_id = %(c)s),"
        " (select status from app.witness_claims where case_id = %(c)s)",
        params(world),
    ).fetchone()
    assert rows == ("stale", "stale")
    admin.execute("update app.facts set status = 'verified' where id = %s", (world["fact"],))
    reviewed = await run_as(api_db, world["expert"], "update app.points set status = 'accepted' returning fact_version")
    assert reviewed == [(4,)]  # the point now refers to the fact as it is after the change


async def test_a_claim_keeps_the_statements_exact_words(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERTS["witness_claims"], params(world))
    with pytest.raises(psycopg.Error) as caught:
        await run_as(api_db, world["expert"], "update app.witness_claims set text_verbatim = '\\x01'")
    assert caught.value.sqlstate == "AV409"


async def test_a_lint_dismissal_needs_a_reason_and_is_kept(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERTS["lint_issues"], insert_params(admin, world, "lint_issues"))
    expert = world["expert"]
    with pytest.raises(psycopg.Error):
        await run_as(api_db, expert, "update app.lint_issues set status = 'dismissed'")
    await run_as(api_db, expert, "update app.lint_issues set status = 'dismissed', dismissed_reason = '\\x00'")
    assert await run_as(api_db, expert, "delete from app.lint_issues returning id") == []
    assert await run_as(api_db, expert, "select dismissed_by from app.lint_issues") == [(expert,)]
    with pytest.raises(psycopg.Error) as reopen:
        await run_as(api_db, expert, "update app.lint_issues set status = 'open'")
    assert reopen.value.sqlstate == "AV409"


async def test_open_lint_issues_are_recalculated_on_save(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERTS["lint_issues"], insert_params(admin, world, "lint_issues"))
    removed = await run_as(api_db, world["expert"], "delete from app.lint_issues returning id")
    assert len(removed) == 1


async def test_a_suggested_unused_fact_is_only_dismissed_by_the_named_expert(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(INSERTS["relevant_facts"], params(world))
    dismiss = "update app.relevant_facts set status = 'dismissed' returning decided_by, version"
    for person in ("co_expert", "assistant", "viewer", "outsider"):
        assert await run_as(api_db, world[person], dismiss) == [], person
    assert await run_as(api_db, world["expert"], dismiss) == [(world["expert"], 2)]
    for sql in (
        "update app.relevant_facts set status = 'suggested'",
        "update app.relevant_facts set why_relevant = 'other words'",
    ):
        with pytest.raises(psycopg.Error) as refused:
            await run_as(api_db, world["expert"], sql)
        assert refused.value.sqlstate == "AV409"


async def test_one_suggestion_per_fact_and_question(admin: psycopg.Connection, world: dict[str, uuid.UUID]) -> None:
    admin.execute(INSERTS["relevant_facts"], params(world))
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin.execute(INSERTS["relevant_facts"], params(world))
