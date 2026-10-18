"""Slice B's out-contract: `citable_facts()` (rule 2, brief "Contracts").

A stub report build asks for the facts it may cite. Only checked facts (verified or edited) on pages
the named expert has read come back: suggested and rejected facts never do, and neither do facts on a
page only the assistant has read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.contracts.facts import citable_facts
from app.core.db import Database
from app.features.reader import repo
from app.features.reader.log import ReaderPageReadLog
from tests.fixtures.stub_facts import load_stub_facts
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


async def build_report(api_db: Database, person: Any, case: Any) -> set[Any]:
    """A stand-in for a report build: the ids of every fact it may cite."""
    async with api_db.request(scope_for(person)) as conn:
        return {f.id for f in await citable_facts(conn, case, ReaderPageReadLog())}


async def test_only_checked_facts_on_pages_the_expert_read_are_citable(
    seed: Seed, api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection
) -> None:
    expert, assistant = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    loaded = await load_stub_facts(aconn, case, pages=[118, 119])
    verified, edited, rejected, *suggested = loaded[118].fact_ids
    on_unread = loaded[119].fact_ids[0]
    for fact, status in ((verified, "verified"), (edited, "edited"), (rejected, "rejected"), (on_unread, "verified")):
        admin.execute("update app.facts set status = %s where id = %s", (status, fact))

    assert await build_report(api_db, expert, case) == set(), "nothing is citable before the expert reads"

    async with api_db.request(scope_for(assistant)) as conn:
        await repo.record_read(conn, case, loaded[119].page_id, assistant, 30)
    async with api_db.request(scope_for(expert)) as conn:
        await repo.record_read(conn, case, loaded[118].page_id, expert, 30)

    assert suggested, "page 118 keeps some unchecked suggestions"
    assert await build_report(api_db, expert, case) == {verified, edited}
