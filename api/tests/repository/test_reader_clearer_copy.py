"""Clearer copies (S10, architecture 10): asking for one, and moving a fact onto one.

- `pages.needs_clearer_copy` starts false; setting it puts the page in slice A's instructing-party
  request draft (`pipeline.repo.clearer_copy_pages`), as an unreadable page already is.
- A fact may move to the page that supersedes its own page (the clearer copy) and nowhere else.
  The move keeps the fact's status and bumps its `version`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.features.pipeline import repo as pipeline_repo
from tests.support.contracts import add_fact
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return seed.case(org, expert, status="active")


@pytest.fixture
async def conn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


async def test_a_page_asked_for_again_joins_the_request_draft(
    conn: psycopg.AsyncConnection, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    doc = seed.document(case)
    plain, asked, unreadable = seed.page(case, doc, 1), seed.page(case, doc, 2), seed.page(case, doc, 3)
    assert admin.execute("select needs_clearer_copy from app.pages where id = %s", (plain,)).fetchone() == (False,)
    admin.execute("update app.pages set needs_clearer_copy = true where id = %s", (asked,))
    admin.execute("update app.pages set unreadable = true where id = %s", (unreadable,))

    assert await pipeline_repo.clearer_copy_pages(conn, case) == [(asked, 2), (unreadable, 3)]


async def test_a_page_set_aside_is_not_asked_for(
    conn: psycopg.AsyncConnection, admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    page = seed.page(case, seed.document(case), 1)
    admin.execute("update app.pages set needs_clearer_copy = true, quarantined = true where id = %s", (page,))
    assert await pipeline_repo.clearer_copy_pages(conn, case) == []


def test_a_verified_fact_moves_to_the_clearer_copy_keeping_its_status(
    admin: psycopg.Connection, seed: Seed, case: uuid.UUID
) -> None:
    doc = seed.document(case)
    old, new = seed.page(case, doc, 1), seed.page(case, doc, 2)
    admin.execute("update app.pages set superseded_by_page_id = %s where id = %s", (new, old))
    fact = add_fact(admin, case, old, status="verified")
    before = admin.execute("select version from app.facts where id = %s", (fact,)).fetchone()
    assert before is not None

    admin.execute("update app.facts set page_id = %s, word_ids = %s where id = %s", (new, ["w9"], fact))

    row = admin.execute("select page_id, status, version from app.facts where id = %s", (fact,)).fetchone()
    assert row == (new, "verified", before[0] + 1)


def test_a_fact_does_not_move_to_any_other_page(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    doc = seed.document(case)
    old, other = seed.page(case, doc, 1), seed.page(case, doc, 2)
    fact = add_fact(admin, case, old, status="verified")
    with pytest.raises(psycopg.Error) as refused:
        admin.execute("update app.facts set page_id = %s where id = %s", (other, fact))
    assert refused.value.sqlstate == "AV409"
