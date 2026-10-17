"""The case setup tables (architecture 4.3, slice C): parties, instructing parties, links, oral
instructions and the subject key.

Row-level security repeats the case access check (rule 9): case members see the rows their case role
allows, people outside the case see nothing, and only roles allowed `case.edit_setup` (or `case.upload`
for instructions) write. Party names and oral instructions are stored encrypted (bytes, never text), and
the subject key is a keyed hash only: the identifier itself can never be stored.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

HASH = "a" * 64
INSERTS = {
    "case_parties": "insert into app.case_parties (case_id, role_key, name) values (%(c)s, 'other_side', '\\x00')",
    "case_instructing_parties": "insert into app.case_instructing_parties (case_id, type_key, name, firm)"
    " values (%(c)s, 'lawyer', 'A Person', 'A Firm')",
    "case_links": "insert into app.case_links (case_id, linked_case_id, kind) values (%(c)s, %(l)s, 'related')",
    "case_instructions": "insert into app.case_instructions (case_id, instruction_version_id, text, received_on)"
    " values (%(c)s, %(v)s, '\\x00', '2024-03-01')",
    "case_subject_keys": f"insert into app.case_subject_keys (case_id, key_hash) values (%(c)s, '{HASH}')",
}
WRITE_ACTION = {"case_instructions": "case.upload"}


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, assistant, viewer, outsider = (seed.person() for _ in range(4))
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant", viewer: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    other = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    row = admin.execute(
        "insert into app.instruction_versions (case_id, version_no, kind) values (%s, 1, 'oral') returning id",
        (case,),
    ).fetchone()
    assert row is not None
    return {
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "outsider": outsider,
        "case": case,
        "other": other,
        "version": row[0],
    }


def params(world: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    return {"c": world["case"], "l": world["other"], "v": world["version"]}


async def insert_as(api_db: Database, person: uuid.UUID, table: str, world: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(person)) as conn:
        await conn.execute(INSERTS[table], params(world))


async def count_as(api_db: Database, person: uuid.UUID, table: str) -> int:
    async with api_db.request(scope_for(person)) as conn:
        row = await (await conn.execute(f"select count(*) from app.{table}")).fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("table", sorted(INSERTS))
async def test_case_people_see_the_rows_and_outsiders_do_not(
    api_db: Database, world: dict[str, uuid.UUID], table: str
) -> None:
    await insert_as(api_db, world["expert"], table, world)
    assert await count_as(api_db, world["expert"], table) == 1
    assert await count_as(api_db, world["viewer"], table) == 1
    assert await count_as(api_db, world["outsider"], table) == 0


@pytest.mark.parametrize("table", sorted(INSERTS))
async def test_a_viewer_cannot_write_and_an_assistant_can(
    api_db: Database, world: dict[str, uuid.UUID], table: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await insert_as(api_db, world["viewer"], table, world)
    await insert_as(api_db, world["assistant"], table, world)


@pytest.mark.parametrize("table", sorted(INSERTS))
def test_the_bare_api_role_sees_nothing(
    api_dsn: str, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    admin.execute(INSERTS[table], params(world))
    with psycopg.connect(api_dsn) as conn:
        assert conn.execute(f"select count(*) from app.{table}").fetchone() == (0,)  # noqa: S608


def test_party_names_and_oral_instructions_are_bytes_not_text(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select table_name, column_name, data_type from information_schema.columns where table_schema = 'app'"
        " and (table_name, column_name) in (('case_parties', 'name'), ('case_parties', 'representative'),"
        " ('case_instructions', 'text'))"
    ).fetchall()
    assert sorted(rows) == [
        ("case_instructions", "text", "bytea"),
        ("case_parties", "name", "bytea"),
        ("case_parties", "representative", "bytea"),
    ]


def test_the_subject_key_holds_only_a_keyed_hash(admin: psycopg.Connection, world: dict[str, uuid.UUID]) -> None:
    columns = {
        r[0]
        for r in admin.execute(
            "select column_name from information_schema.columns where table_schema = 'app'"
            " and table_name = 'case_subject_keys'"
        ).fetchall()
    }
    assert columns == {"case_id", "key_hash", "created_at", "updated_at"}
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "insert into app.case_subject_keys (case_id, key_hash) values (%s, 'ZZ-ID-123456')", (world["case"],)
        )


def test_a_case_cannot_link_to_itself(admin: psycopg.Connection, world: dict[str, uuid.UUID]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "insert into app.case_links (case_id, linked_case_id, kind) values (%s, %s, 'related')",
            (world["case"], world["case"]),
        )


def test_an_oral_instruction_belongs_to_a_version_of_its_own_case(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin.execute(INSERTS["case_instructions"], {**params(world), "c": world["other"]})


async def test_instructing_parties_are_never_deleted(api_db: Database, world: dict[str, uuid.UUID]) -> None:
    await insert_as(api_db, world["expert"], "case_instructing_parties", world)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.request(scope_for(world["expert"])) as conn:
            await conn.execute("delete from app.case_instructing_parties where case_id = %s", (world["case"],))


async def test_parties_are_fixed_once_the_case_is_served(
    api_db: Database, admin: psycopg.Connection, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    org = seed.org(kind="personal", members={world["expert"]: "owner"})
    served = seed.case(org, world["expert"], status="served")
    admin.execute(INSERTS["case_parties"], {"c": served})
    for statement in (
        "update app.case_parties set role_key = 'instructing_side' where case_id = %s",
        "delete from app.case_parties where case_id = %s",
    ):
        with pytest.raises(psycopg.Error) as caught:
            async with api_db.request(scope_for(world["expert"])) as conn:
                await conn.execute(statement, (served,))
        assert caught.value.sqlstate == "AV409"
