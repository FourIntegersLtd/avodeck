"""How a page is turned for reading (`page_turns`, migration 0213; page viewer item 1).

- One row per page (no `case_id` column, as `word_corrections`): row-level security finds the case
  through `pages`. Case viewers read it; people allowed `page.rotate` set it, as themselves.
- Only a quarter turn is stored (0, 90, 180, 270). Nobody deletes a turn; "upright" is 0.
- The worker reads only its own job's case.
- Deleting a case (`app.purge_case`) leaves no turns: they go with their pages.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.features.reader import repo
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, assistant, viewer, stranger = seed.person(), seed.person(), seed.person(), seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    seed.case_member(case, viewer, "viewer", added_by=expert)
    page = seed.page(case, seed.document(case), 1)
    return {
        "case": case,
        "org": org,
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "stranger": stranger,
        "page_id": page,
    }


async def set_turn(api_db: Database, w: dict[str, Any], person: str, degrees: int) -> None:
    async with api_db.request(scope_for(w[person])) as conn:
        await repo.set_turn(conn, w["page_id"], degrees, w[person])


async def test_the_turn_is_kept_once_per_page_and_replaced(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    await set_turn(api_db, world, "expert", 90)
    await set_turn(api_db, world, "assistant", 180)
    rows = admin.execute(
        "select degrees, person_id from app.page_turns where page_id = %s", (world["page_id"],)
    ).fetchall()
    assert rows == [(180, world["assistant"])]


async def test_viewers_see_the_turn_strangers_see_none(api_db: Database, world: dict[str, Any]) -> None:
    await set_turn(api_db, world, "expert", 270)
    for person, seen in (("expert", 1), ("assistant", 1), ("viewer", 1), ("stranger", 0)):
        async with api_db.request(scope_for(world[person])) as conn:
            cur = await conn.execute("select count(*) from app.page_turns where page_id = %s", (world["page_id"],))
            assert await cur.fetchone() == (seen,), person


async def test_only_people_allowed_to_turn_pages_set_a_turn_as_themselves(
    api_db: Database, world: dict[str, Any]
) -> None:
    sql = "insert into app.page_turns (page_id, degrees, person_id) values (%s, 90, %s)"
    for person, as_person in (("viewer", "viewer"), ("stranger", "stranger"), ("assistant", "expert")):
        async with api_db.request(scope_for(world[person])) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(sql, (world["page_id"], world[as_person]))


async def test_only_quarter_turns_and_no_deleting(api_db: Database, world: dict[str, Any]) -> None:
    await set_turn(api_db, world, "expert", 90)
    async with api_db.request(scope_for(world["expert"])) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            await conn.execute("update app.page_turns set degrees = 45 where page_id = %s", (world["page_id"],))
    async with api_db.request(scope_for(world["expert"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await conn.execute("delete from app.page_turns where page_id = %s", (world["page_id"],))


async def test_the_worker_reads_only_its_own_jobs_case(
    api_db: Database, worker_dsn: str, seed: Seed, world: dict[str, Any]
) -> None:
    await set_turn(api_db, world, "expert", 90)
    other = seed.case(world["org"], world["expert"], status="active")
    for job_case, seen in ((world["case"], 1), (other, 0)):
        with psycopg.connect(worker_dsn) as conn:
            conn.execute("select set_config('app.job_case_id', %s, false)", (str(job_case),))
            row = conn.execute("select count(*) from app.page_turns where page_id = %s", (world["page_id"],)).fetchone()
        assert row == (seen,)


async def test_deleting_the_case_leaves_no_turns(
    api_db: Database, admin: psycopg.Connection, worker_dsn: str, world: dict[str, Any]
) -> None:
    await set_turn(api_db, world, "expert", 180)
    admin.execute("update app.cases set status = 'served' where id = %s", (world["case"],))
    admin.execute(
        "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%s, %s, 'zz-key')"
        " on conflict (case_id) do nothing",
        (world["case"], b"wrapped"),
    )
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(world["case"]),))
        conn.execute("select app.purge_case(%s)", (world["case"],))
    row = admin.execute("select count(*) from app.page_turns where page_id = %s", (world["page_id"],)).fetchone()
    assert row == (0,)
