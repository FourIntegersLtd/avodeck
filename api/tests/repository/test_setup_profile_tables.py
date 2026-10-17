"""The expert profile tables (architecture 4.1, slice C): roles, training, templates, declarations, signatures.

Each row belongs to one person. Row-level security lets only that person see or change it: another
person sees nothing and cannot write a row in someone else's name, and the bare API role sees nothing.
A declaration can no longer be added to or changed once the expert has a served report (409).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.contracts.case_tables import CASE_TABLES
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

INSERTS = {
    "expert_roles": "insert into app.expert_roles (expert_id, specialty, role_title, setting, period)"
    " values (%(p)s, 'zz-specialty', 'Practitioner', 'practitioner', daterange('2020-01-01', null))",
    "expert_training": "insert into app.expert_training (expert_id, title, year) values (%(p)s, 'A course', 2020)",
    "report_templates": "insert into app.report_templates (owner_type, owner_id, filename, storage_path, sha256,"
    " size_bytes, version) values ('person', %(p)s, 't.docx', 'people/x/t', repeat('0', 64), 10, 1)",
    "declarations": "insert into app.declarations (expert_id, jurisdiction, kind, text)"
    " values (%(p)s, 'zz-test', 'affirmation', 'More words.')",
    "signatures": "insert into app.signatures (expert_id, method, storage_path, sha256)"
    " values (%(p)s, 'drawn', 'people/x/s', repeat('0', 64))",
}
OWNER = {"report_templates": "owner_id"}


@pytest.fixture
def people(seed: Seed) -> dict[str, uuid.UUID]:
    return {n: seed.person() for n in ("expert", "other")}


async def insert_as(api_db: Database, person: uuid.UUID, table: str, owner: uuid.UUID) -> None:
    async with api_db.request(scope_for(person)) as conn:
        await conn.execute(INSERTS[table], {"p": owner})


async def count_as(api_db: Database, person: uuid.UUID, table: str) -> int:
    async with api_db.request(scope_for(person)) as conn:
        row = await (await conn.execute(f"select count(*) from app.{table}")).fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("table", sorted(INSERTS))
async def test_a_person_sees_and_writes_only_their_own_rows(
    api_db: Database, people: dict[str, uuid.UUID], table: str
) -> None:
    await insert_as(api_db, people["expert"], table, people["expert"])
    assert await count_as(api_db, people["expert"], table) == 1
    assert await count_as(api_db, people["other"], table) == 0


@pytest.mark.parametrize("table", sorted(INSERTS))
async def test_nobody_writes_a_row_in_someone_elses_name(
    api_db: Database, people: dict[str, uuid.UUID], table: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await insert_as(api_db, people["other"], table, people["expert"])


@pytest.mark.parametrize("table", sorted(INSERTS))
def test_the_bare_api_role_sees_nothing(
    api_dsn: str, admin: psycopg.Connection, people: dict[str, uuid.UUID], table: str
) -> None:
    owner = OWNER.get(table, "expert_id")
    admin.execute(INSERTS[table], {"p": people["expert"]})
    assert admin.execute(f"select count(*) from app.{table} where {owner} = %s", (people["expert"],)).fetchone() == (1,)  # noqa: S608
    with psycopg.connect(api_dsn) as conn:
        assert conn.execute(f"select count(*) from app.{table}").fetchone() == (0,)  # noqa: S608


def test_profile_tables_are_not_case_tables() -> None:
    assert not set(INSERTS) & set(CASE_TABLES)


def test_a_role_cannot_end_before_it_starts(admin: psycopg.Connection, people: dict[str, uuid.UUID]) -> None:
    with pytest.raises(psycopg.errors.DataException):
        admin.execute(
            "insert into app.expert_roles (expert_id, specialty, role_title, period)"
            " values (%s, 'zz-specialty', 'Practitioner', daterange('2020-01-01', '2019-01-01'))",
            (people["expert"],),
        )


def test_a_role_needs_a_start_date(admin: psycopg.Connection, people: dict[str, uuid.UUID]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "insert into app.expert_roles (expert_id, specialty, role_title, period)"
            " values (%s, 'zz-specialty', 'Practitioner', daterange(null, '2019-01-01'))",
            (people["expert"],),
        )


async def test_declarations_change_until_the_first_served_report(
    api_db: Database, seed: Seed, people: dict[str, uuid.UUID]
) -> None:
    expert = people["expert"]
    org = seed.org(kind="personal", members={expert: "owner"})
    seed.case(org, expert, status="active")
    await insert_as(api_db, expert, "declarations", expert)
    async with api_db.request(scope_for(expert)) as conn:
        await conn.execute("update app.declarations set text = 'Changed.' where expert_id = %s", (expert,))

    seed.case(org, expert, status="served")
    with pytest.raises(psycopg.Error) as caught:
        async with api_db.request(scope_for(expert)) as conn:
            await conn.execute("update app.declarations set text = 'Again.' where expert_id = %s", (expert,))
    assert caught.value.sqlstate == "AV409"
    with pytest.raises(psycopg.Error) as caught:
        async with api_db.request(scope_for(expert)) as conn:
            await conn.execute(
                "insert into app.declarations (expert_id, jurisdiction, kind, text)"
                " values (%s, 'zz-test', 'other_kind', 'New.')",
                (expert,),
            )
    assert caught.value.sqlstate == "AV409"


async def test_another_experts_served_report_does_not_lock_mine(
    api_db: Database, seed: Seed, people: dict[str, uuid.UUID]
) -> None:
    org = seed.org(kind="team", members={people["expert"]: "owner", people["other"]: "expert"})
    seed.case(org, people["other"], status="served")
    await insert_as(api_db, people["expert"], "declarations", people["expert"])
    assert await count_as(api_db, people["expert"], "declarations") == 1
