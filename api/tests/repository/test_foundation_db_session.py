"""core/db.py: every request and job runs in one transaction with SET LOCAL session settings (ADR 0013)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest

from app.core.db import Database, RequestScope, UnsafeDatabaseRole

pytestmark = pytest.mark.anyio

PERSON_A = uuid.UUID("00000000-0000-4000-8000-00000000000a")
PERSON_B = uuid.UUID("00000000-0000-4000-8000-00000000000b")
ORG = uuid.UUID("00000000-0000-4000-8000-0000000000f1")
CASE = uuid.UUID("00000000-0000-4000-8000-0000000000c1")


@pytest.fixture
def probe(admin: psycopg.Connection) -> Iterator[str]:
    """A throwaway table whose RLS policy shows a row only to the person in app.person_id."""
    admin.execute(
        """
        create table app.zz_rls_probe (
          id uuid primary key default gen_random_uuid(),
          person_id uuid not null
        );
        alter table app.zz_rls_probe enable row level security;
        create policy zz_rls_probe_own on app.zz_rls_probe
          using (person_id = app.current_person_id())
          with check (person_id = app.current_person_id());
        """
    )
    admin.execute("insert into app.zz_rls_probe (person_id) values (%s), (%s)", (PERSON_A, PERSON_B))
    try:
        yield "app.zz_rls_probe"
    finally:
        admin.execute("drop table app.zz_rls_probe")


@pytest.fixture
async def db(api_dsn: str) -> AsyncIterator[Database]:
    database = Database(api_dsn, max_size=1)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def scope(person: uuid.UUID, orgs: tuple[uuid.UUID, ...] = (ORG,)) -> RequestScope:
    return RequestScope(person_id=person, org_ids=orgs, request_id="req-test-1")


async def test_request_sets_person_orgs_and_request_id_for_the_transaction(db: Database) -> None:
    async with db.request(scope(PERSON_A)) as conn:
        cur = await conn.execute(
            "select app.current_person_id(), app.current_org_ids(), current_setting('app.request_id', true)"
        )
        assert await cur.fetchone() == (PERSON_A, [ORG], "req-test-1")


async def test_settings_do_not_leak_to_the_next_transaction_on_the_same_connection(db: Database) -> None:
    async with db.request(scope(PERSON_A)):
        pass
    async with db.job(CASE) as conn:  # max_size=1, so this reuses the same connection
        cur = await conn.execute("select app.current_person_id(), app.current_org_ids()")
        assert await cur.fetchone() == (None, [])


async def test_job_sets_only_the_job_case(db: Database) -> None:
    async with db.job(CASE) as conn:
        cur = await conn.execute("select app.current_job_case_id(), app.current_person_id()")
        assert await cur.fetchone() == (CASE, None)


async def test_rls_shows_only_the_acting_persons_rows(db: Database, probe: str) -> None:
    async with db.request(scope(PERSON_A)) as conn:
        cur = await conn.execute(f"select person_id from {probe}")
        assert await cur.fetchall() == [(PERSON_A,)]


async def test_without_session_settings_nothing_is_visible(api_dsn: str, probe: str) -> None:
    async with await psycopg.AsyncConnection.connect(api_dsn) as conn:
        cur = await conn.execute(f"select count(*) from {probe}")
        assert await cur.fetchone() == (0,)


async def test_an_error_rolls_the_whole_request_back(db: Database, probe: str, admin: psycopg.Connection) -> None:
    with pytest.raises(RuntimeError):
        async with db.request(scope(PERSON_A)) as conn:
            await conn.execute(f"insert into {probe} (person_id) values (%s)", (PERSON_A,))
            raise RuntimeError("boom")
    (count,) = admin.execute(f"select count(*) from {probe}").fetchone()  # type: ignore[misc]
    assert count == 2


async def test_rls_refuses_writing_a_row_for_someone_else(db: Database, probe: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with db.request(scope(PERSON_A)) as conn:
            await conn.execute(f"insert into {probe} (person_id) values (%s)", (PERSON_B,))


async def test_refuses_to_open_as_a_role_that_bypasses_rls(admin_dsn: str) -> None:
    database = Database(admin_dsn, max_size=1)
    with pytest.raises(UnsafeDatabaseRole):
        await database.open()
    await database.close()


async def test_worker_role_opens(worker_dsn: str) -> None:
    database = Database(worker_dsn, max_size=1)
    await database.open()
    try:
        async with database.job(CASE) as conn:
            cur = await conn.execute("select current_user")
            assert await cur.fetchone() == ("app_worker",)
    finally:
        await database.close()
