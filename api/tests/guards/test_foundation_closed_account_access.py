"""Rule 9 and scenario S15: a closed account keeps read-only sign-in for its served named cases.

With `people.closed_at` set, a person may take only `CLOSED_ACCOUNT_ACTIONS` (reading) on a case
that has been served and on which they are the named expert. Every other action and every other
case is 404, as if it did not exist. The database (`app.can_access_case()`) answers the same.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.auth.access import CLOSED_ACCOUNT_ACTIONS, check_case_access
from app.core.auth.permissions import CASE_ACTIONS
from app.core.db import Database
from app.core.errors import ApiError
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

READS = {"case.view_metadata", "case.view_content", "case.view_audit", "case.view_certificate"}


async def answer(api_db: Database, person: uuid.UUID, case: uuid.UUID, action: str) -> tuple[int, bool]:
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute("select app.can_access_case(%s, %s)", (case, action))
        row = await cur.fetchone()
        assert row is not None
        try:
            await check_case_access(conn, case, action)
        except ApiError as err:
            return err.status, row[0]
    return 200, row[0]


def test_closed_accounts_only_read() -> None:
    assert set(CLOSED_ACCOUNT_ACTIONS) == READS


@pytest.mark.parametrize("status", ["served", "settled"])
async def test_a_closed_account_reads_its_served_named_cases(api_db: Database, seed: Seed, status: str) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status=status)
    seed.close_account(expert)
    for action in sorted(CASE_ACTIONS):
        want = (200, True) if action in READS else (404, False)
        assert await answer(api_db, expert, case, action) == want, action


@pytest.mark.parametrize("status", ["draft", "active", "withdrawn"])
async def test_a_closed_account_sees_nothing_on_cases_never_served(api_db: Database, seed: Seed, status: str) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status=status)
    seed.close_account(expert)
    for action in sorted(CASE_ACTIONS):
        assert await answer(api_db, expert, case, action) == (404, False), action


async def test_a_closed_account_keeps_nothing_through_case_or_organisation_roles(api_db: Database, seed: Seed) -> None:
    """Only the named expert keeps reading: not an assistant, not an organisation owner."""
    expert, helper = seed.person(), seed.person()
    team = seed.org(kind="team", members={expert: "expert", helper: "owner"})
    case = seed.case(team, expert, status="served", members={helper: "assistant"})
    seed.close_account(helper)
    for action in sorted(CASE_ACTIONS):
        assert await answer(api_db, helper, case, action) == (404, False), action


async def test_an_open_account_is_unchanged(api_db: Database, seed: Seed) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="served")
    assert await answer(api_db, expert, case, "case.export") == (200, True)
