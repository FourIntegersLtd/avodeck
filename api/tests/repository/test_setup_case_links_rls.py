"""A case link needs access to both cases (slice C review finding 6, rule 9).

Row-level security on `case_links` insert checks `case.edit_setup` on the case and that the linked case is
one the person can see (`case.view_metadata`) or one in the same organisation, so nobody can link to (and
so learn of) a case in another organisation that they cannot see.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LINK = "insert into app.case_links (case_id, linked_case_id, kind) values (%s, %s, 'related')"


async def test_a_link_to_a_case_you_cannot_see_is_refused(api_db: Database, seed: Seed) -> None:
    expert, stranger = seed.person(), seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    other_org = seed.org(kind="personal", members={stranger: "owner"})
    case, mine = seed.case(org, expert, status="active"), seed.case(org, expert, status="active")
    hidden = seed.case(other_org, stranger, status="active")

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.request(scope_for(expert)) as conn:
            await conn.execute(LINK, (case, hidden))
    async with api_db.request(scope_for(expert)) as conn:
        await conn.execute(LINK, (case, mine))
        row = await (await conn.execute("select count(*) from app.case_links where case_id = %s", (case,))).fetchone()
    assert row == (1,)
