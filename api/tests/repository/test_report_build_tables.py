"""Slice F's build tables (migration 0601): the expert's own words for report sections, and one build
record per draft (which template and citation style it used, whether the template fell back, page count).

Section words are the named expert's (`opinion.write`), sealed under the case key, readable to people on the
case. Build records come only from the worker and never change. Required contents the expert ticked are
kept on the case's report settings.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.contracts import add_report_version
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SECTION = (
    "insert into app.report_section_texts (case_id, section, body, citation_refs)"
    " values (%(c)s, 'zz_section', '\\x00', '[]')"
)
BUILD = (
    "insert into app.report_builds (report_version_id, case_id, citation_style, template_fallback, page_count)"
    " values (%(r)s, %(c)s, 'page', false, 3)"
)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, co_expert, assistant, viewer, outsider = (seed.person() for _ in range(5))
    org = seed.org(
        kind="team", members={expert: "expert", co_expert: "expert", assistant: "assistant", viewer: "expert"}
    )
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(
        org, expert, status="active", members={co_expert: "co_expert", assistant: "assistant", viewer: "viewer"}
    )
    return {
        "expert": expert,
        "co_expert": co_expert,
        "assistant": assistant,
        "viewer": viewer,
        "outsider": outsider,
        "case": case,
        "draft": add_report_version(admin, case),
    }


async def run_as(
    api_db: Database, person: uuid.UUID, sql: str, values: Mapping[str, Any] | None = None
) -> list[tuple[object, ...]]:
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, values)
        return await cur.fetchall() if cur.description else []


def values(world: dict[str, uuid.UUID]) -> dict[str, uuid.UUID]:
    return {"c": world["case"], "r": world["draft"]}


async def test_only_the_named_expert_writes_section_words_and_the_case_reads_them(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    for person in ("co_expert", "assistant", "viewer", "outsider"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world[person], SECTION, values(world))
    await run_as(api_db, world["expert"], SECTION, values(world))
    count = "select count(*) from app.report_section_texts"
    for person in ("expert", "co_expert", "assistant", "viewer"):
        assert await run_as(api_db, world[person], count) == [(1,)], person
    assert await run_as(api_db, world["outsider"], count) == [(0,)]
    changed = await run_as(
        api_db, world["assistant"], "update app.report_section_texts set body = '\\x01' returning section"
    )
    assert changed == []
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], "delete from app.report_section_texts")


async def test_build_records_come_from_the_worker_and_never_change(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], BUILD, values(world))
    admin.execute(BUILD, values(world))
    assert await run_as(api_db, world["viewer"], "select page_count from app.report_builds") == [(3,)]
    assert await run_as(api_db, world["outsider"], "select page_count from app.report_builds") == []
    for sql in ("update app.report_builds set page_count = 4", "delete from app.report_builds"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world["expert"], sql)


async def test_ticked_required_contents_are_kept_on_the_report_settings(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    await run_as(
        api_db,
        world["expert"],
        "insert into app.report_settings (case_id, addressed_contents) values (%(c)s, '{zz_item}')",
        values(world),
    )
    assert await run_as(api_db, world["viewer"], "select addressed_contents from app.report_settings") == [
        (["zz_item"],)
    ]
