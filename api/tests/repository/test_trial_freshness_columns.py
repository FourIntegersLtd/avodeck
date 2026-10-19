"""Migration 0902: what a trial pack or offline copy was built from, and offline copies going stale.

- Both tables keep the hearing version, the last page number and the newest served version they were built
  from (`trial_freshness.built_from`); an offline copy also keeps its court bundle.
- An offline copy has `stale`, `stale_reason` (a short key, never text) and `stale_at`, like a trial pack;
  once stale it never becomes current again (a new copy is made instead).
- Who may mark them is unchanged: the expert who signs for copies, anyone who uploads for packs.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SNAPSHOT = ["built_hearing_version", "built_last_page_no", "built_served_version_id"]


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant, viewer = (seed.person() for _ in range(3))
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant", viewer: "expert"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    return {"expert": expert, "assistant": assistant, "viewer": viewer, "case": case}


async def run_as(
    api_db: Database, person: uuid.UUID, sql: str, values: Mapping[str, Any] | None = None
) -> list[tuple[object, ...]]:
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, values)
        return await cur.fetchall() if cur.description else []


def columns(admin: psycopg.Connection, table: str) -> dict[str, tuple[str, str, str | None]]:
    rows = admin.execute(
        "select column_name, data_type, is_nullable, column_default from information_schema.columns"
        " where table_schema = 'app' and table_name = %s",
        (table,),
    ).fetchall()
    return {r[0]: (r[1], r[2], r[3]) for r in rows}


def package(admin: psycopg.Connection, case: uuid.UUID) -> uuid.UUID:
    row = admin.execute(
        "insert into app.offline_packages (case_id, device_label, format_version, expires_at)"
        " values (%s, 'Zz tablet', 1, now() + interval '1 day') returning id",
        (case,),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def test_packs_and_copies_keep_what_they_were_built_from(admin: psycopg.Connection) -> None:
    packs, copies = columns(admin, "trial_packs"), columns(admin, "offline_packages")
    for name in SNAPSHOT:
        assert packs[name][1] == "YES" and copies[name][1] == "YES"
    assert packs["built_hearing_version"][0] == copies["built_last_page_no"][0] == "integer"
    assert packs["built_served_version_id"][0] == "uuid" and copies["court_bundle_id"][0] == "uuid"
    assert packs["stale_at"][0] == "timestamp with time zone"


def test_an_offline_copy_has_a_stale_flag_with_a_short_reason(admin: psycopg.Connection, seed: Seed) -> None:
    copies = columns(admin, "offline_packages")
    assert copies["stale"] == ("boolean", "NO", "false")
    assert copies["stale_reason"][0] == "text" and copies["stale_at"][0] == "timestamp with time zone"
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    made = package(admin, case)
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(
            "update app.offline_packages set stale = true, stale_reason = 'Has words in it' where id = %s", (made,)
        )


async def test_only_the_expert_who_signs_marks_a_copy_stale_and_it_stays_stale(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    made = package(admin, world["case"])
    mark = "update app.offline_packages set stale = true, stale_reason = 'hearing_changed', stale_at = now()"
    values = {"p": made}
    await run_as(api_db, world["assistant"], mark + " where id = %(p)s", values)
    assert admin.execute("select stale from app.offline_packages where id = %s", (made,)).fetchone() == (False,)
    await run_as(api_db, world["expert"], mark + " where id = %(p)s", values)
    row = admin.execute("select stale, stale_reason from app.offline_packages where id = %s", (made,)).fetchone()
    assert row == (True, "hearing_changed")
    with pytest.raises(psycopg.errors.CheckViolation):
        await run_as(api_db, world["expert"], "update app.offline_packages set stale = false where id = %(p)s", values)


async def test_whoever_marks_a_pack_stale_also_records_when(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    row = admin.execute(
        "insert into app.trial_packs (case_id, sections, sha256, storage_path, page_count)"
        " values (%s, '{report}', %s, 'zz', 1) returning id",
        (world["case"], "c" * 64),
    ).fetchone()
    assert row is not None
    await run_as(
        api_db,
        world["assistant"],
        "update app.trial_packs set stale = true, stale_reason = 'court_bundle_superseded', stale_at = now()"
        " where id = %(p)s",
        {"p": row[0]},
    )
    marked = admin.execute("select stale, stale_at is not null from app.trial_packs where id = %s", (row[0],))
    assert marked.fetchone() == (True, True)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(
            api_db, world["expert"], "update app.trial_packs set built_last_page_no = 1 where id = %(p)s", {"p": row[0]}
        )
