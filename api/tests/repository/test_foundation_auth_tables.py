"""Auth tables (architecture 4.1): sessions, recovery codes and email changes are private to their
person under row-level security, and the bare roles see nothing (ADR 0013)."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed, sha256_hex

pytestmark = pytest.mark.anyio
AUTH_TABLES = ("sessions", "mfa_recovery_codes", "email_changes")


def _seed_rows(seed: Seed, admin: psycopg.Connection, person: uuid.UUID) -> None:
    seed.session(person)
    admin.execute(
        "insert into app.mfa_recovery_codes (person_id, code_hash) values (%s, %s)",
        (person, sha256_hex(f"{person}:{uuid.uuid4()}")),
    )
    admin.execute(
        "insert into app.email_changes (person_id, new_email, old_token_hash, new_token_hash) values (%s, %s, %s, %s)",
        (person, f"{person.hex[:8]}@new.test", sha256_hex(str(uuid.uuid4())), sha256_hex(str(uuid.uuid4()))),
    )


@pytest.mark.parametrize("table", AUTH_TABLES)
def test_auth_table_has_row_level_security(admin: psycopg.Connection, table: str) -> None:
    row = admin.execute(
        "select c.relrowsecurity from pg_class c join pg_namespace n on n.oid = c.relnamespace"
        " where n.nspname = 'app' and c.relname = %s",
        (table,),
    ).fetchone()
    assert row == (True,), table


@pytest.mark.parametrize("table", AUTH_TABLES)
async def test_each_person_sees_only_their_own_auth_rows(
    api_db: Database, seed: Seed, admin: psycopg.Connection, table: str
) -> None:
    me, other = seed.person(), seed.person()
    _seed_rows(seed, admin, me)
    _seed_rows(seed, admin, other)
    async with api_db.request(scope_for(me)) as conn:
        cur = await conn.execute(f"select distinct person_id from app.{table}")  # noqa: S608
        assert await cur.fetchall() == [(me,)]


@pytest.mark.parametrize("table", AUTH_TABLES)
async def test_without_a_person_no_auth_rows_are_visible(
    api_db: Database, seed: Seed, admin: psycopg.Connection, table: str
) -> None:
    _seed_rows(seed, admin, seed.person())
    async with api_db.anonymous() as conn:
        cur = await conn.execute(f"select count(*) from app.{table}")  # noqa: S608
        assert await cur.fetchone() == (0,)


async def test_a_person_cannot_mark_another_persons_session_signed_in(
    api_db: Database, seed: Seed, admin: psycopg.Connection
) -> None:
    me, other = seed.person(), seed.person()
    theirs = seed.session(other, aal="aal1", mfa_age=None)
    async with api_db.request(scope_for(me)) as conn:
        cur = await conn.execute("update app.sessions set aal = 'aal2' where id = %s", (theirs.id,))
        assert cur.rowcount == 0
    assert admin.execute("select aal from app.sessions where id = %s", (theirs.id,)).fetchone() == ("aal1",)


def test_recovery_codes_are_unique_per_person(seed: Seed, admin: psycopg.Connection) -> None:
    person = seed.person()
    code = sha256_hex(f"{person}:abcde-fghij")
    admin.execute("insert into app.mfa_recovery_codes (person_id, code_hash) values (%s, %s)", (person, code))
    with pytest.raises(psycopg.errors.UniqueViolation):
        admin.execute("insert into app.mfa_recovery_codes (person_id, code_hash) values (%s, %s)", (person, code))
