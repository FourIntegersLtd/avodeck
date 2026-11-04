"""Slice A: the repair job re-dates an entry whose date is a birth date and splits a card into its dated lines.

The case has no recorded date of birth: the birth date is found by its label on the page (rule 11: the label
words come from the pack). No model is called. Runs under the launch and the fixture packs."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import harness
from tests.support.db import scope_for
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed
from tests.unit.test_pipeline_entry_dates_births import label_of, written

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_repair_redates_a_birth_dated_entry_and_splits_a_card_with_no_recorded_birth_date(
    combo: Combo,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    pack = default_library().combine(*combo.codes)
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    page_id = seed.page(case_id, seed.document(case_id), 1)
    ecg = ["ASHWORTH", label_of(pack), written(pack, 14, 3, 1969), "08-JUN-2022", "14:51"]
    card = [
        ["Seen", "27-Sep-2018", "cough"],
        ["plan", "rest"],
        ["Seen", "03-Oct-2018", "better"],
        ["Seen", "21-Nov-2018", "home"],
    ]
    last = ["Seen", "03-Apr-2019", "by", "nurse"]
    rows = [ecg, *card, last]
    words = [w for row in rows for w in row]
    sizes = [len(ecg), sum(len(r) for r in card), len(last)]
    already = dt.date(2020, 1, 1)

    def entry(first: int, size: int, when: dt.date | None, basis: str | None) -> uuid.UUID:
        got = admin.execute(
            "insert into app.record_entries"
            " (page_id, bundle_id, kind, first_n, last_n, event_date, date_basis, extraction_version)"
            " values (%s, (select bundle_id from app.cases where id = %s), 'consultation', %s, %s, %s, %s, 'v1')"
            " returning id",
            (page_id, case_id, first, first + size - 1, when, basis),
        ).fetchone()
        assert got is not None
        return uuid.UUID(str(got[0]))

    wrong = entry(1, sizes[0], dt.date(1969, 3, 14), "printed")  # dated with the birth date
    undated = entry(1 + sizes[0], sizes[1], None, None)
    fine = entry(1 + sizes[0] + sizes[1], sizes[2], already, "printed")
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=None,
    ) as h:
        from app.features.pipeline.paths import word_purpose

        async with api_db.request(scope_for(expert)) as conn:
            line_of = [i for i, row in enumerate(rows) for _ in row]
            for n, text in enumerate(words, start=1):
                top = 10 * line_of[n - 1]
                sealed = await h.crypto.encrypt(conn, case_id, text.encode(), purpose=word_purpose(page_id, f"w{n}"))
                admin.execute(
                    "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
                    " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, %s, 0.9, 'ocr')",
                    (page_id, case_id, n, f"w{n}", sealed, [0, top, 1, top, 1, top + 5, 0, top + 5]),
                )
        resp = await h.client.post(f"/v1/cases/{case_id}/repair-fact-dates")
        assert resp.status_code == 202
        await h.drain()

        def row(entry_id: uuid.UUID) -> tuple[Any, ...]:
            got = admin.execute(
                "select event_date, date_basis, first_n, last_n from app.record_entries where id = %s", (entry_id,)
            )
            return got.fetchone() or ()

        assert row(wrong)[:2] == (dt.date(2022, 6, 8), "printed"), "the birth date is replaced by the trace's date"
        assert row(fine)[:2] == (already, "printed"), "an entry with its own date is never changed"
        got = admin.execute(
            "select event_date, date_basis, first_n, last_n from app.record_entries where page_id = %s"
            " and first_n >= %s and last_n < %s order by first_n",
            (page_id, 1 + sizes[0], 1 + sizes[0] + sizes[1]),
        ).fetchall()
        assert [(r[0], r[1]) for r in got] == [
            (dt.date(2018, 9, 27), "printed"),
            (dt.date(2018, 10, 3), "printed"),
            (dt.date(2018, 11, 21), "printed"),
        ], "the card is one entry per dated line"
        assert got[0][2] == 1 + sizes[0] and got[-1][3] == sizes[0] + sizes[1], "the pieces cover the card"
        assert all(b[2] == a[3] + 1 for a, b in zip(got, got[1:], strict=False))
        assert row(undated)[2] == 1 + sizes[0], "the card's first row keeps its place"
