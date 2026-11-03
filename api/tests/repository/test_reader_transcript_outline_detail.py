"""Slice B: the transcript outline says what each section holds (transcript-document plan, step 0.1).

Each section gives the earliest and latest dated entry, how many distinct bundle pages its entries come from,
and its years as runs of consecutive entries (the undated ones last) with where each run starts in the paged
list. Counts, dates and positions only (rule 3); no text is opened (rule 6).
"""

from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
import pytest

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import default_library
from app.features.reader import transcript
from tests.support.db import scope_for
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed
from tests.support.transcript_world import Spec, add_document, add_page

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
LIBRARY = default_library()


@pytest.fixture
async def aconn(admin_dsn: str) -> Any:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_a_section_says_its_dates_pages_and_years(
    api_db: Database, aconn: psycopg.AsyncConnection, seed: Seed, combo: Combo
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, packs=combo.case_row())
    doc = await add_document(aconn, case)
    await add_page(
        aconn, CRYPTO, case, doc, 1,
        [Spec("zz_session", ("first",), day=date(2019, 3, 1)), Spec("zz_session", ("second",), day=date(2019, 7, 2))],
    )  # fmt: skip
    await add_page(aconn, CRYPTO, case, doc, 2, [Spec("zz_session", ("third",), day=date(2021, 1, 5))])
    await add_page(aconn, CRYPTO, case, doc, 3, [Spec("zz_session", ("fourth",))])
    async with api_db.request(scope_for(expert)) as conn:
        got = await transcript.outline(conn, LIBRARY, case)
    assert len(got.sections) == 1  # the four entries share a kind and a source, so one section holds them
    key, _, count, offset = got.sections[0]
    assert count == 4
    detail = got.details[key]
    assert detail.first_date == date(2019, 3, 1)
    assert detail.last_date == date(2021, 1, 5)
    assert detail.pages == 3
    assert detail.years == [(2019, offset, 2), (2021, offset + 2, 1), (None, offset + 3, 1)]


async def test_a_section_with_no_dated_entry_has_no_dates(
    api_db: Database, aconn: psycopg.AsyncConnection, seed: Seed
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    doc = await add_document(aconn, case)
    await add_page(aconn, CRYPTO, case, doc, 1, [Spec("zz_session", ("only",))])
    async with api_db.request(scope_for(expert)) as conn:
        got = await transcript.outline(conn, LIBRARY, case)
    detail = got.details[got.sections[0][0]]
    assert (detail.first_date, detail.last_date, detail.pages) == (None, None, 1)
    assert detail.years == [(None, got.sections[0][3], 1)]
