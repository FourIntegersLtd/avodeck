"""Slice A: repair_fact_dates job fills event_date from printed words (F12)."""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from app.features.pipeline import dates
from tests.repository.test_pipeline_support import harness
from tests.support.facts_seed import insert_fact
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


async def test_repair_job_sets_event_date_from_printed_words(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> None:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    doc = seed.document(case_id)
    page_id = seed.page(case_id, doc, 1)
    admin.execute(
        "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
        " values (%s, %s, 1, 'w1', '03/04/2024', '{0,0,1,0,1,1,0,1}', 1, 'ocr'),"
        " (%s, %s, 2, 'w2', 'review', '{0,0,1,0,1,1,0,1}', 1, 'ocr')",
        (page_id, case_id, page_id, case_id),
    )
    pack = default_library().combine(*LAUNCH.codes)
    kind = pack.fact_kinds()[0]["key"]
    field = dates.date_field(pack, kind)
    assert field is not None
    fact_id = insert_fact(
        admin,
        case_id,
        page_id,
        kind,
        fields={field: "03/04/2024"},
        value="review",
        word_ids=["w1", "w2"],
        event_date=None,
        status="verified",
    )
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
        resp = await h.client.post(f"/v1/cases/{case_id}/repair-fact-dates")
        assert resp.status_code == 202
        await h.drain()
        row = admin.execute("select event_date, date_basis from app.facts where id = %s", (fact_id,)).fetchone()
        assert row == (dt.date(2024, 4, 3), "printed")
