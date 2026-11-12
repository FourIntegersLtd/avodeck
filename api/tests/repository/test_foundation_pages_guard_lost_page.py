"""The page guard lets a lost page become readable once it has been cut again and read (audit 13, DP-09 review P1).

A page no parser child could cut or draw is marked `unreadable` with no image and no text. A later run cuts it
(an image) and reads it (words). Migration 0074 lets the flag clear then, and only then: the page was never read
before (`text_source` was unset), now has an image, words and a text source. Any other clear still needs the
expert's transcription (0050). Ids and counts only (rule 6).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


class Page:
    def __init__(self, admin: psycopg.Connection, seed: Seed) -> None:
        expert = seed.person()
        self.admin = admin
        self.case = seed.case(seed.org(members={expert: "owner"}), expert)
        self.id = seed.page(self.case, seed.document(self.case), 1)

    def lost(self, *, image: bool = False) -> Page:
        self.admin.execute(
            "update app.pages set unreadable = true, image_path = %s where id = %s",
            ("zz/page.png" if image else None, self.id),
        )
        return self

    def read_before(self) -> Page:
        self.admin.execute("update app.pages set text_source = 'ocr' where id = %s", (self.id,))
        return self

    def words(self) -> Page:
        self.admin.execute(
            "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, handwritten,"
            " struck, source) select id, bundle_id, 1, 'zzw1', 'zz', array[0,0,1,0,1,1,0,1]::float8[], 0.9,"
            " false, false, 'ocr' from app.pages where id = %s",
            (self.id,),
        )
        return self

    def clear(self, *, text_source: str | None = "ocr") -> None:
        self.admin.execute(
            "update app.pages set unreadable = false, text_source = coalesce(%s, text_source) where id = %s",
            (text_source, self.id),
        )

    def unreadable(self) -> bool:
        (value,) = self.admin.execute("select unreadable from app.pages where id = %s", (self.id,)).fetchone()  # type: ignore[misc]
        return bool(value)


@pytest.fixture
def page(admin: psycopg.Connection, seed: Seed) -> Page:
    return Page(admin, seed)


def refused(page: Page, **kwargs: str | None) -> None:
    with pytest.raises(psycopg.Error) as caught:
        page.clear(**kwargs)
    assert caught.value.sqlstate == "AV409"
    assert page.unreadable() is True


def test_a_lost_page_cut_again_and_read_becomes_readable(page: Page) -> None:
    page.lost(image=True).words().clear()
    assert page.unreadable() is False


def test_a_lost_page_with_no_words_stays_unreadable(page: Page) -> None:
    page.lost(image=True)
    refused(page)


def test_a_lost_page_with_no_image_stays_unreadable(page: Page) -> None:
    page.lost(image=False).words()
    refused(page)


def test_clearing_the_flag_without_recording_a_read_is_refused(page: Page) -> None:
    page.lost(image=True).words()
    refused(page, text_source=None)


def test_a_page_that_was_read_and_found_unreadable_stays_so_when_read_again(page: Page) -> None:
    page.read_before().lost(image=True).words()
    refused(page)


def test_the_expert_transcription_still_clears_it(admin: psycopg.Connection, page: Page) -> None:
    page.read_before().lost(image=False)
    admin.execute(
        "update app.pages set unreadable = false, transcribed_by_expert_text = 'zz' where id = %s", (page.id,)
    )
    assert page.unreadable() is False


async def test_the_worker_role_can_clear_a_lost_page_it_has_cut_and_read(page: Page, worker_db: Database) -> None:
    page.lost(image=True).words()
    async with worker_db.job(page.case) as conn:
        await conn.execute("update app.pages set unreadable = false, text_source = 'ocr' where id = %s", (page.id,))
    assert page.unreadable() is False


async def test_the_worker_role_cannot_clear_a_page_with_no_words(page: Page, worker_db: Database) -> None:
    page.lost(image=True)
    with pytest.raises(psycopg.Error) as caught:
        async with worker_db.job(page.case) as conn:
            await conn.execute("update app.pages set unreadable = false, text_source = 'ocr' where id = %s", (page.id,))
    assert caught.value.sqlstate == "AV409"
    assert page.unreadable() is True
