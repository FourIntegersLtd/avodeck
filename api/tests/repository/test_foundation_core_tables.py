"""Core tables (architecture 4.1-4.3, 4.4, 4.11) exist and row-level security shows each person
only what the permissions matrix allows; the bare roles see nothing (ADR 0013)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

CORE_TABLES = (
    "people",
    "experts",
    "organisations",
    "memberships",
    "cases",
    "case_members",
    "documents",
    "pages",
    "jobs",
    "notifications",
    "notification_preferences",
    "feature_flags",
    "audit_log",
    "idempotency_keys",
)
CASE_TABLES = ("documents", "pages", "jobs", "case_members")
BUNDLE_SCOPED_TABLES = ("documents", "pages")


def _case_scoped_count_sql(table: str) -> str:
    if table in BUNDLE_SCOPED_TABLES:
        return f"select count(*) from app.{table} where bundle_id = (select bundle_id from app.cases where id = %s)"
    return f"select count(*) from app.{table} where case_id = %s"


@pytest.mark.parametrize("table", CORE_TABLES)
def test_core_table_exists_in_the_app_schema(admin: psycopg.Connection, table: str) -> None:
    (found,) = admin.execute("select to_regclass(%s) is not null", (f"app.{table}",)).fetchone()  # type: ignore[misc]
    assert found, table


def test_cases_carry_the_four_pack_seams_and_the_named_expert(admin: psycopg.Connection) -> None:
    cols = {
        r[0]
        for r in admin.execute(
            "select column_name from information_schema.columns where table_schema = 'app' and table_name = 'cases'"
        ).fetchall()
    }
    assert {
        "organisation_id",
        "named_expert_id",
        "jurisdiction",
        "case_type",
        "specialty",
        "expert_role",
        "pack_versions",
        "label",
        "status",
        "on_hold",
        "party_role",
        "instructing_party_type",
        "court_track",
        "due_date",
        "delete_after",
        "version",
        "updated_at",
    } <= cols


@dataclass
class Fixture:
    case: uuid.UUID
    other_case: uuid.UUID
    expert: uuid.UUID
    admin_person: uuid.UUID
    outsider: uuid.UUID
    team: uuid.UUID


@pytest.fixture
def world(seed: Seed) -> Fixture:
    expert, admin_person, outsider, other = seed.person(), seed.person(), seed.person(), seed.person()
    team = seed.org(kind="team", members={expert: "expert", admin_person: "admin"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(team, expert, members={other: "viewer"})
    other_case = seed.case(team, other)
    for c in (case, other_case):
        doc = seed.document(c)
        seed.page(c, doc, 1)
        seed.job(c)
    seed.notification(expert, team, case)
    return Fixture(case, other_case, expert, admin_person, outsider, team)


async def _count(db: Database, person: uuid.UUID, sql: str, *args: object) -> int:
    async with db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, args)
        (n,) = await cur.fetchone()  # type: ignore[misc]
        return int(n)


@pytest.mark.parametrize("role_dsn", ["api_dsn", "worker_dsn"])
@pytest.mark.parametrize("table", CORE_TABLES)
def test_bare_runtime_roles_see_nothing(
    request: pytest.FixtureRequest, world: Fixture, seed: Seed, role_dsn: str, table: str
) -> None:
    seed.flag(f"flag-{uuid.uuid4().hex[:6]}", "global")
    with psycopg.connect(request.getfixturevalue(role_dsn)) as conn:
        (n,) = conn.execute(f"select count(*) from app.{table}").fetchone()  # type: ignore[misc]
    assert n == 0, table


pytestmark_anyio = pytest.mark.anyio


@pytestmark_anyio
@pytest.mark.parametrize("table", CASE_TABLES)
async def test_named_expert_sees_case_rows_and_an_outsider_sees_none(
    api_db: Database, world: Fixture, table: str
) -> None:
    sql = _case_scoped_count_sql(table)
    assert await _count(api_db, world.expert, sql, world.case) >= 1
    assert await _count(api_db, world.outsider, sql, world.case) == 0


@pytestmark_anyio
async def test_org_admin_not_on_the_case_sees_metadata_but_no_pages(api_db: Database, world: Fixture) -> None:
    assert await _count(api_db, world.admin_person, "select count(*) from app.cases where id = %s", world.case) == 1
    for table in ("documents", "pages"):
        sql = _case_scoped_count_sql(table)
        assert await _count(api_db, world.admin_person, sql, world.case) == 0, table


@pytestmark_anyio
async def test_outsider_cannot_see_the_case_or_its_organisation(api_db: Database, world: Fixture) -> None:
    assert await _count(api_db, world.outsider, "select count(*) from app.cases where id = %s", world.case) == 0
    assert await _count(api_db, world.outsider, "select count(*) from app.organisations where id = %s", world.team) == 0
    sql = "select count(*) from app.memberships where organisation_id = %s"
    assert await _count(api_db, world.outsider, sql, world.team) == 0


@pytestmark_anyio
async def test_people_see_their_own_notifications_only(api_db: Database, world: Fixture) -> None:
    sql = "select count(*) from app.notifications where case_id = %s"
    assert await _count(api_db, world.expert, sql, world.case) == 1
    assert await _count(api_db, world.admin_person, sql, world.case) == 0


@pytestmark_anyio
async def test_a_person_sees_their_own_people_row_only(api_db: Database, world: Fixture) -> None:
    assert await _count(api_db, world.expert, "select count(*) from app.people") == 1


@pytestmark_anyio
async def test_feature_flags_visible_by_scope(api_db: Database, world: Fixture, seed: Seed) -> None:
    key = f"flag-{uuid.uuid4().hex[:8]}"
    seed.flag(key, "global")
    seed.flag(key, "organisation", world.team)
    seed.flag(key, "person", world.expert)
    seed.flag(key, "person", world.outsider)
    sql = "select count(*) from app.feature_flags where key = %s"
    assert await _count(api_db, world.expert, sql, key) == 3
    assert await _count(api_db, world.outsider, sql, key) == 2


@pytestmark_anyio
async def test_a_job_sees_only_its_own_case(api_db: Database, worker_dsn: str, world: Fixture) -> None:
    worker = Database(worker_dsn, max_size=1)
    await worker.open()
    try:
        async with worker.job(world.case) as conn:
            cur = await conn.execute("select bundle_id from app.cases where id = %s", (world.case,))
            row = await cur.fetchone()
            assert row is not None
            (bundle_id,) = row
            cur = await conn.execute("select count(distinct bundle_id), min(bundle_id::text) from app.pages")
            assert await cur.fetchone() == (1, str(bundle_id))
            cur = await conn.execute("select count(*) from app.cases")
            assert await cur.fetchone() == (1,)
    finally:
        await worker.close()


@pytestmark_anyio
async def test_a_viewer_cannot_add_documents(api_db: Database, world: Fixture, seed: Seed) -> None:
    viewer = seed.person()
    seed.membership(world.team, viewer, "viewer")
    seed.case_member(world.case, viewer, "viewer", added_by=world.expert)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.request(scope_for(viewer)) as conn:
            await conn.execute(
                "insert into app.documents (bundle_id, kind, filename, sha256)"
                " values ((select bundle_id from app.cases where id = %s), 'records', 'x.pdf', %s)",
                (world.case, "1" * 64),
            )
