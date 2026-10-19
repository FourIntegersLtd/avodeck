"""Slice I's tables (migration 0900): who may read and write them and the status changes they allow.

- People on the case read; only those whose case role allows the action write (RLS repeats the route check).
- A joint statement moves draft -> out_for_signature -> signed (or back to draft), or draft ->
  signed_external; a signed statement never changes again, nor do its agenda items.
- A built trial pack changes only to go stale; nothing in these tables is deleted by the API role.
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

MEETING = (
    "insert into app.expert_meetings (case_id, type, discussion_on, sign_by, sign_rule_ref, send_by, send_rule_ref,"
    " declarations) values (%(c)s, 'zz_conference', '2026-11-02', '2026-11-09', 'Rule Z.9', '2026-11-23',"
    " 'Rule Z.9', '[]') returning id"
)


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, viewer, outsider = (seed.person() for _ in range(3))
    org = seed.org(kind="team", members={expert: "expert", viewer: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={viewer: "viewer"})
    return {"expert": expert, "viewer": viewer, "outsider": outsider, "case": case}


async def run_as(
    api_db: Database, person: uuid.UUID, sql: str, values: Mapping[str, Any] | None = None
) -> list[tuple[object, ...]]:
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, values)
        return await cur.fetchall() if cur.description else []


async def test_people_on_the_case_read_and_only_the_expert_writes(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    values = {"c": world["case"]}
    [(meeting,)] = await run_as(api_db, world["expert"], MEETING, values)
    seen = "select id from app.expert_meetings where case_id = %(c)s"
    assert await run_as(api_db, world["viewer"], seen, values) == [(meeting,)]
    assert await run_as(api_db, world["outsider"], seen, values) == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["viewer"], MEETING, values)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], "delete from app.expert_meetings where id = %(m)s", {"m": meeting})


async def test_a_signed_statement_never_changes(api_db: Database, world: dict[str, uuid.UUID]) -> None:
    values: dict[str, Any] = {"c": world["case"]}
    [(meeting,)] = await run_as(api_db, world["expert"], MEETING, values)
    values["m"] = meeting
    move = "update app.expert_meetings set status = %(s)s where id = %(m)s"
    with pytest.raises(psycopg.errors.CheckViolation):
        await run_as(api_db, world["expert"], move, {**values, "s": "signed"})
    await run_as(api_db, world["expert"], move, {**values, "s": "signed_external"})
    for status in ("draft", "out_for_signature"):
        with pytest.raises(psycopg.errors.CheckViolation):
            await run_as(api_db, world["expert"], move, {**values, "s": status})
    rows = await run_as(api_db, world["expert"], "select version from app.expert_meetings where id = %(m)s", values)
    assert rows == [(2,)]


async def test_a_built_trial_pack_only_goes_stale(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    row = admin.execute(
        "insert into app.trial_packs (case_id, sections, sha256, storage_path, page_count)"
        " values (%s, '{report}', %s, 'x', 3) returning id",
        (world["case"], "b" * 64),
    ).fetchone()
    assert row is not None
    values = {"p": row[0]}
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], "update app.trial_packs set sha256 = 'x' where id = %(p)s", values)
    await run_as(
        api_db,
        world["expert"],
        "update app.trial_packs set stale = true, stale_reason = 'court_bundle_superseded' where id = %(p)s",
        values,
    )
