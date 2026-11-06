"""Page-number ranges on the three "what is left to do" queries (ADR 0038, build step 7).

`pages_without_words`, `pages_to_extract` and `pages_to_embed` take optional `first` and `last` page numbers (both
ends included). A page-batch child passes its batch's range; without them each returns what it returns today.
Numbers and ids only (rule 6).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import psycopg
import pytest

from app.core.db import Database
from app.features.pipeline import repo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

COUNT = 6


@dataclass(frozen=True)
class World:
    case: uuid.UUID
    pages: dict[int, uuid.UUID]  # bundle page number -> id


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def unread(admin: psycopg.Connection, seed: Seed) -> World:
    """Six pages with an image and no words yet."""
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert)
    document = seed.document(case)
    pages = {n: seed.page(case, document, n) for n in range(1, COUNT + 1)}
    for page in pages.values():
        admin.execute("update app.pages set image_path = 'zz/image' where id = %s", (page,))
    return World(case, pages)


@pytest.fixture
def read(admin: psycopg.Connection, unread: World) -> World:
    """The same pages with their words read (so they wait for extraction and for an embedding)."""
    for page in unread.pages.values():
        admin.execute("update app.pages set text_source = 'embedded' where id = %s", (page,))
    return unread


def numbers(w: World, ids: list[uuid.UUID]) -> list[int]:
    by_id = {page: n for n, page in w.pages.items()}
    return [by_id[i] for i in ids]


async def test_pages_without_words_with_a_range_returns_only_that_range(worker_db: Database, unread: World) -> None:
    async with worker_db.job(unread.case) as conn:
        got = await repo.pages_without_words(conn, unread.case, first=2, last=4)
    assert numbers(unread, got) == [2, 3, 4]


async def test_pages_without_words_without_a_range_returns_every_page(worker_db: Database, unread: World) -> None:
    async with worker_db.job(unread.case) as conn:
        got = await repo.pages_without_words(conn, unread.case)
    assert numbers(unread, got) == [1, 2, 3, 4, 5, 6]


async def test_pages_to_extract_with_a_range_returns_only_that_range(worker_db: Database, read: World) -> None:
    async with worker_db.job(read.case) as conn:
        got = await repo.pages_to_extract(conn, read.case, first=3, last=5)
    assert [n for _, n in got] == [3, 4, 5]


async def test_pages_to_extract_without_a_range_returns_every_page(worker_db: Database, read: World) -> None:
    async with worker_db.job(read.case) as conn:
        got = await repo.pages_to_extract(conn, read.case)
    assert [n for _, n in got] == [1, 2, 3, 4, 5, 6]


async def test_pages_to_embed_with_a_range_returns_only_that_range(worker_db: Database, read: World) -> None:
    async with worker_db.job(read.case) as conn:
        got = await repo.pages_to_embed(conn, read.case, "zz-model", first=1, last=2)
    assert numbers(read, got) == [1, 2]


async def test_pages_to_embed_without_a_range_returns_every_page(worker_db: Database, read: World) -> None:
    async with worker_db.job(read.case) as conn:
        got = await repo.pages_to_embed(conn, read.case, "zz-model")
    assert numbers(read, got) == [1, 2, 3, 4, 5, 6]


async def test_a_range_that_holds_one_end_only_still_works(worker_db: Database, read: World) -> None:
    """`first` alone or `last` alone is an open-ended range (a range is both or neither in practice)."""
    async with worker_db.job(read.case) as conn:
        from_four = await repo.pages_to_extract(conn, read.case, first=4)
        to_two = await repo.pages_to_extract(conn, read.case, last=2)
    assert [n for _, n in from_four] == [4, 5, 6]
    assert [n for _, n in to_two] == [1, 2]
