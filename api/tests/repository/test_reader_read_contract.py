"""The read log and check counts other slices read through `app.core.contracts.reading`.

- `page_read_log(conn, case_id)`: every read of the case in read order, with the bundle page number,
  who read it, when, how long, and whether it counts for citing (slice H's export, slice F's
  methodology appendix). Runs on the caller's connection: someone with no access to the case sees none.
- `verification_counts(conn, case_id)`: how many checks of each kind (`fact_verifications.action`).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from app.core.contracts.reading import PageReadEntry, page_read_log, verification_counts
from app.core.db import Database
from app.features.reader import repo
from tests.support.contracts import add_fact
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, assistant, stranger = seed.person(), seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    doc = seed.document(case)
    pages = {n: seed.page(case, doc, n) for n in (4, 9)}
    return {"case": case, "expert": expert, "assistant": assistant, "stranger": stranger, "pages": pages}


async def read(api_db: Database, w: dict[str, Any], person: uuid.UUID, n: int, dwell: int) -> None:
    async with api_db.request(scope_for(person)) as conn:
        await repo.record_read(conn, w["case"], w["pages"][n], person, dwell)


async def test_the_read_log_lists_every_read_in_order(api_db: Database, world: dict[str, Any]) -> None:
    await read(api_db, world, world["expert"], 9, 30)
    await read(api_db, world, world["assistant"], 4, 12)
    await read(api_db, world, world["expert"], 4, 45)
    async with api_db.request(scope_for(world["expert"])) as conn:
        log = await page_read_log(conn, world["case"])
    assert all(isinstance(e, PageReadEntry) for e in log)
    assert [(e.page_no, e.person_id, e.dwell_seconds, e.counts_for_citing) for e in log] == [
        (9, world["expert"], 30, True),
        (4, world["assistant"], 12, False),
        (4, world["expert"], 45, True),
    ]
    assert log[0].read_at <= log[1].read_at <= log[2].read_at


async def test_someone_without_access_sees_no_reads_or_counts(api_db: Database, world: dict[str, Any]) -> None:
    await read(api_db, world, world["expert"], 9, 30)
    async with api_db.request(scope_for(world["stranger"])) as conn:
        assert await page_read_log(conn, world["case"]) == []
        assert await verification_counts(conn, world["case"]) == {}


async def test_check_counts_are_given_per_kind_of_check(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    page = world["pages"][4]
    facts = [add_fact(admin, world["case"], page) for _ in range(3)]

    def row(fact_id: uuid.UUID) -> dict[str, Any]:
        return {"id": fact_id, "value": "9 ug/L", "fields": {}, "word_ids": ["w1", "w2"], "page_id": page}

    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await verification_counts(conn, world["case"]) == {}
        for fact_id, action in zip(facts, ("correct", "correct", "not_right"), strict=True):
            await repo.log_check(conn, world["case"], world["expert"], action, row(fact_id), row(fact_id))
        await repo.log_check(conn, world["case"], world["expert"], "edit", row(facts[2]), row(facts[2]))
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await verification_counts(conn, world["case"]) == {"correct": 2, "not_right": 1, "edit": 1}
