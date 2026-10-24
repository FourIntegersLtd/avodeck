"""The expert's own bookmarks and private notes (`page_notes`, migration 0214; page viewer item 10).

- One row per person and page (no `case_id` column, as `page_turns`): row-level security finds the case
  through `pages`. A person sees, adds, changes and removes only their own rows, and only on a case where
  they may read pages (`page.read`).
- The worker cannot read the table at all, so a note never reaches an AI task.
- Deleting a case (`app.purge_case`) leaves no notes: they go with their pages.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ADD = "insert into app.page_notes (page_id, person_id, bookmarked) values (%s, %s, true)"


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, assistant, viewer, stranger = seed.person(), seed.person(), seed.person(), seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    seed.case_member(case, viewer, "viewer", added_by=expert)
    page = seed.page(case, seed.document(case), 1)
    return {"case": case, "expert": expert, "assistant": assistant, "viewer": viewer, "stranger": stranger, "page_id": page}


async def add(api_db: Database, w: dict[str, Any], person: str) -> None:
    async with api_db.request(scope_for(w[person])) as conn:
        await conn.execute(ADD, (w["page_id"], w[person]))


async def count(api_db: Database, w: dict[str, Any], person: str) -> int:
    async with api_db.request(scope_for(w[person])) as conn:
        cur = await conn.execute("select count(*) from app.page_notes where page_id = %s", (w["page_id"],))
        row = await cur.fetchone()
    assert row is not None
    return int(row[0])


async def test_each_person_sees_only_their_own(api_db: Database, world: dict[str, Any]) -> None:
    await add(api_db, world, "expert")
    await add(api_db, world, "assistant")
    for person, seen in (("expert", 1), ("assistant", 1), ("viewer", 0), ("stranger", 0)):
        assert await count(api_db, world, person) == seen, person


async def test_nobody_keeps_a_note_for_someone_else_or_without_page_read(api_db: Database, world: dict[str, Any]) -> None:
    for person, as_person in (("viewer", "viewer"), ("stranger", "stranger"), ("assistant", "expert")):
        async with api_db.request(scope_for(world[person])) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(ADD, (world["page_id"], world[as_person]))


async def test_one_row_per_person_and_page_and_nobody_changes_anothers(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    await add(api_db, world, "assistant")
    async with api_db.request(scope_for(world["assistant"])) as conn:
        with pytest.raises(psycopg.errors.UniqueViolation):
            await conn.execute(ADD, (world["page_id"], world["assistant"]))
    async with api_db.request(scope_for(world["expert"])) as conn:
        await conn.execute("update app.page_notes set bookmarked = false where page_id = %s", (world["page_id"],))
        await conn.execute("delete from app.page_notes where page_id = %s", (world["page_id"],))
    row = admin.execute("select bookmarked from app.page_notes where page_id = %s", (world["page_id"],)).fetchall()
    assert row == [(True,)]


async def test_the_worker_cannot_read_notes(api_db: Database, worker_dsn: str, world: dict[str, Any]) -> None:
    await add(api_db, world, "expert")
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(world["case"]),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select count(*) from app.page_notes")


async def test_deleting_the_case_leaves_no_notes(
    api_db: Database, admin: psycopg.Connection, worker_dsn: str, world: dict[str, Any]
) -> None:
    await add(api_db, world, "expert")
    admin.execute("update app.cases set status = 'served' where id = %s", (world["case"],))
    admin.execute(
        "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%s, %s, 'zz-key')"
        " on conflict (case_id) do nothing",
        (world["case"], b"wrapped"),
    )
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(world["case"]),))
        conn.execute("select app.purge_case(%s)", (world["case"],))
    assert admin.execute("select count(*) from app.page_notes").fetchone() == (0,)
