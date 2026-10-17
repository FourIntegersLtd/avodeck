"""Display names of a case's people (`app.core.contracts.members`; request from slice C).

Row-level security on `people` hides other people's rows, so a slice cannot read a colleague's
name itself. `case_member_names(conn, case_id)` gives the named expert's and every current case
member's display name to anyone who may see the case (`case.view_metadata`), and nothing to anyone
else. Removed members are not listed. Names are personal data: never logged (rule 6).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.contracts.members import case_member_names
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def team(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    t = {
        "expert": seed.person(full_name="Zed Expert"),
        "assistant": seed.person(full_name="Zed Helper"),
        "gone": seed.person(full_name="Zed Gone"),
        "admin": seed.person(full_name="Zed Admin"),
        "outsider": seed.person(full_name="Zed Outsider"),
    }
    t["org"] = seed.org(
        kind="team",
        members={t["expert"]: "expert", t["assistant"]: "assistant", t["gone"]: "assistant", t["admin"]: "admin"},
    )
    seed.org(kind="personal", members={t["outsider"]: "owner"})
    t["case"] = seed.case(t["org"], t["expert"], members={t["assistant"]: "assistant", t["gone"]: "assistant"})
    admin.execute("update app.case_members set removed_at = now() where person_id = %s", (t["gone"],))
    return t


@pytest.mark.parametrize("person", ["expert", "assistant", "admin"])
async def test_anyone_who_may_see_the_case_gets_the_names_of_its_people(
    person: str, api_db: Database, team: dict[str, uuid.UUID]
) -> None:
    async with api_db.request(scope_for(team[person])) as conn:
        names = await case_member_names(conn, team["case"])
    assert names == {team["expert"]: "Zed Expert", team["assistant"]: "Zed Helper"}


async def test_someone_outside_the_case_gets_no_names(api_db: Database, team: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(team["outsider"])) as conn:
        assert await case_member_names(conn, team["case"]) == {}


async def test_a_person_without_a_name_is_listed_without_one(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    admin.execute("update app.people set full_name = null where id = %s", (team["assistant"],))
    async with api_db.request(scope_for(team["expert"])) as conn:
        names = await case_member_names(conn, team["case"])
    assert names[team["assistant"]] is None
