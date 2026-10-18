"""Slice D's tables and their row-level security (docs/slices/D-understand.md, rules 1 and 9).

- fact_question_links: any reader may suggest; only the named expert confirms, rejects or adds by
  hand. A decided link never goes back to suggested. One link per fact and question.
- availability_overrides: append-only, named expert only, a reason always, a date or "not known".
- findings: written by the `rerun_checks` job; only the named expert decides; "not relevant" and
  "explained elsewhere" need a reason; the job never sets or clears a decision; at least one fact.
- consultation_notes: the named expert's own note on a consultation, one per consultation.
- comments: any reader may comment; only the author changes the words; anyone who may comment
  may resolve.
- check_config: each person sees and changes only their own settings.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


class World:
    def __init__(self, seed: Seed) -> None:
        self.seed = seed
        self.expert, self.assistant, self.stranger = seed.person(), seed.person(), seed.person()
        org = seed.org(kind="team", members={self.expert: "expert", self.assistant: "assistant"})
        self.case = seed.case(org, self.expert, status="active", members={self.assistant: "assistant"})
        self.page = seed.page(self.case, seed.document(self.case), 1)

    def fact(self, kind: str = "result", status: str = "verified") -> uuid.UUID:
        row = self.seed.admin.execute(
            "insert into app.facts (case_id, page_id, kind, value, word_ids, event_date) "
            "values (%s, %s, %s, '9', '{w1}', '2024-02-14') returning id",
            (self.case, self.page, kind),
        ).fetchone()
        assert row is not None
        if status != "suggested":
            self.seed.admin.execute("update app.facts set status = %s where id = %s", (status, row[0]))
        return uuid.UUID(str(row[0]))

    def question(self) -> uuid.UUID:
        row = self.seed.admin.execute(
            "insert into app.questions (case_id, number, text_verbatim, kind) "
            "values (%s, 1, 'Was it acted on?', 'breach') returning id",
            (self.case,),
        ).fetchone()
        assert row is not None
        return uuid.UUID(str(row[0]))


@pytest.fixture
def world(seed: Seed) -> World:
    return World(seed)


async def _run(db: Database, person: uuid.UUID, sql: str, args: tuple[Any, ...]) -> int:
    async with db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, args)
        return cur.rowcount


@contextmanager
def _refused() -> Iterator[None]:
    """A guard trigger said no (`app.refuse`, SQLSTATE AV409)."""
    with pytest.raises(psycopg.Error) as caught:
        yield
    assert caught.value.sqlstate == "AV409"


SUGGEST = (
    "insert into app.fact_question_links (case_id, fact_id, question_id, source, reason) "
    "values (%s, %s, %s, 'ai', 'same test') returning id"
)


# --- fact_question_links -------------------------------------------------------------------------


async def test_a_reader_suggests_and_only_the_named_expert_decides(api_db: Database, world: World) -> None:
    fact, question = world.fact(), world.question()
    assert await _run(api_db, world.assistant, SUGGEST, (world.case, fact, question)) == 1
    confirm = "update app.fact_question_links set status = 'confirmed' where fact_id = %s"
    assert await _run(api_db, world.assistant, confirm, (fact,)) == 0
    assert await _run(api_db, world.expert, confirm, (fact,)) == 1


async def test_only_the_named_expert_adds_a_link_by_hand(api_db: Database, world: World) -> None:
    fact, question = world.fact(), world.question()
    manual = (
        "insert into app.fact_question_links (case_id, fact_id, question_id, source, status) "
        "values (%s, %s, %s, 'expert', 'confirmed')"
    )
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await _run(api_db, world.assistant, manual, (world.case, fact, question))
    assert await _run(api_db, world.expert, manual, (world.case, fact, question)) == 1


async def test_a_decided_link_never_goes_back_to_suggested_and_pairs_are_unique(api_db: Database, world: World) -> None:
    fact, question = world.fact(), world.question()
    await _run(api_db, world.expert, SUGGEST, (world.case, fact, question))
    reject = "update app.fact_question_links set status = 'rejected' where fact_id = %s"
    await _run(api_db, world.expert, reject, (fact,))
    with _refused():
        await _run(
            api_db, world.expert, "update app.fact_question_links set status = 'suggested' where fact_id = %s", (fact,)
        )
    with pytest.raises(psycopg.errors.UniqueViolation):
        await _run(api_db, world.expert, SUGGEST, (world.case, fact, question))


async def test_strangers_see_no_links(api_db: Database, world: World) -> None:
    fact, question = world.fact(), world.question()
    await _run(api_db, world.expert, SUGGEST, (world.case, fact, question))
    assert await _run(api_db, world.stranger, "select 1 from app.fact_question_links", ()) == 0


# --- availability_overrides ----------------------------------------------------------------------

OVERRIDE = (
    "insert into app.availability_overrides (case_id, fact_id, available_at, unknown, reason, person_id) "
    "values (%s, %s, %s, %s, %s, %s)"
)


async def test_an_override_is_the_named_expert_s_with_a_reason_and_never_changes(
    api_db: Database, world: World
) -> None:
    fact = world.fact("letter")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await _run(api_db, world.assistant, OVERRIDE, (world.case, fact, "2024-05-20", False, "stamp", world.assistant))
    with pytest.raises(psycopg.errors.CheckViolation):
        await _run(api_db, world.expert, OVERRIDE, (world.case, fact, "2024-05-20", False, "", world.expert))
    with pytest.raises(psycopg.errors.CheckViolation):
        await _run(api_db, world.expert, OVERRIDE, (world.case, fact, "2024-05-20", True, "both", world.expert))
    assert await _run(api_db, world.expert, OVERRIDE, (world.case, fact, None, True, "no stamp", world.expert)) == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        sql = "update app.availability_overrides set reason = 'x' where fact_id = %s"
        await _run(api_db, world.expert, sql, (fact,))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await _run(api_db, world.expert, "delete from app.availability_overrides where fact_id = %s", (fact,))


# --- findings ------------------------------------------------------------------------------------

FINDING = (
    "insert into app.findings (case_id, check_key, rule, evidence_fact_ids, pack_version) "
    "values (%s, 'abnormal_no_action', 'no_action_within', %s, 'v1') returning id"
)


async def _finding(worker_db: Database, case: uuid.UUID, facts: list[uuid.UUID]) -> uuid.UUID:
    async with worker_db.job(case) as conn:
        row = await (await conn.execute(FINDING, (case, facts))).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def test_the_job_writes_findings_and_only_the_named_expert_decides(
    api_db: Database, worker_db: Database, world: World
) -> None:
    finding = await _finding(worker_db, world.case, [world.fact()])
    decide = "update app.findings set decision = %s, reason = %s where id = %s"
    assert await _run(api_db, world.assistant, decide, ("relevant", None, finding)) == 0
    assert await _run(api_db, world.expert, decide, ("relevant", None, finding)) == 1
    with _refused():
        await _run(api_db, world.expert, decide, ("not_relevant", " ", finding))
    assert await _run(api_db, world.expert, decide, ("explained_elsewhere", "see page 4", finding)) == 1


async def test_the_job_never_sets_or_clears_a_decision(api_db: Database, worker_db: Database, world: World) -> None:
    finding = await _finding(worker_db, world.case, [world.fact()])
    with _refused():
        async with worker_db.job(world.case) as conn:
            await conn.execute("update app.findings set decision = 'relevant' where id = %s", (finding,))
    await _run(api_db, world.expert, "update app.findings set decision = 'relevant' where id = %s", (finding,))
    with _refused():
        async with worker_db.job(world.case) as conn:
            await conn.execute("update app.findings set decision = null where id = %s", (finding,))
    async with worker_db.job(world.case) as conn:
        await conn.execute("update app.findings set status = 'gone' where id = %s", (finding,))


async def test_a_finding_rests_on_at_least_one_fact(worker_db: Database, world: World) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        await _finding(worker_db, world.case, [])


# --- consultation_notes, comments, check_config --------------------------------------------------


async def test_only_the_named_expert_notes_a_consultation_once(api_db: Database, world: World) -> None:
    visit = world.fact("consultation")
    note = "insert into app.consultation_notes (case_id, fact_id, body, person_id) values (%s, %s, 'my note', %s)"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await _run(api_db, world.assistant, note, (world.case, visit, world.assistant))
    assert await _run(api_db, world.expert, note, (world.case, visit, world.expert)) == 1
    with pytest.raises(psycopg.errors.UniqueViolation):
        await _run(api_db, world.expert, note, (world.case, visit, world.expert))


async def test_only_the_author_changes_a_comment_and_any_reader_resolves_it(api_db: Database, world: World) -> None:
    comment = (
        "insert into app.comments (case_id, target_kind, target_id, body, author_id) "
        "values (%s, 'page', %s, 'look here', %s) returning id"
    )
    async with api_db.request(scope_for(world.assistant)) as conn:
        row = await (await conn.execute(comment, (world.case, str(world.page), world.assistant))).fetchone()
    assert row is not None
    with _refused():
        await _run(api_db, world.expert, "update app.comments set body = 'changed' where id = %s", (row[0],))
    own = "update app.comments set body = 'look again' where id = %s"
    assert await _run(api_db, world.assistant, own, (row[0],)) == 1
    resolve = "update app.comments set resolved_at = now(), resolved_by = %s where id = %s"
    assert await _run(api_db, world.expert, resolve, (world.expert, row[0])) == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await _run(api_db, world.expert, comment, (world.case, str(world.page), world.assistant))


async def test_check_settings_are_each_person_s_own(api_db: Database, world: World) -> None:
    mine = "insert into app.check_config (person_id, check_key, enabled) values (%s, 'a', false)"
    assert await _run(api_db, world.expert, mine, (world.expert,)) == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await _run(api_db, world.assistant, mine, (world.expert,))
    assert await _run(api_db, world.assistant, "select 1 from app.check_config", ()) == 0
