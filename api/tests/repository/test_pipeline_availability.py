"""Slice A: availability evidence from T-07b (architecture 4.4, 6.2): when a page was received,
filed, seen or sent to the clinician.

The model points at words; code checks them like a fact's. An item citing words not on the page is
dropped, an item whose date is not printed in its cited words is dropped, and the date is read by
code in the pack's date order. A date that could be read more than one way is kept with no date:
never guessed. Runs a typed page under both pack combinations; messages name pages only.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import harness, text_pdf
from tests.support.ai import ScriptedBedrock, response
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

READ_AS = {"DMY": dt.date(2024, 4, 3), "MDY": dt.date(2024, 3, 4)}  # 03/04/2024 under each date order
PRINTED = {"DMY": "22/05/2024", "MDY": "05/22/2024"}  # 22 May 2024 under each date order


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


def _answer(evidence: list[dict[str, Any]]) -> Any:
    return response(
        {
            "source_type": None,
            "printed_dates": [],
            "facts": [],
            "references_out": [],
            "availability_evidence": evidence,
            "actions_requested_of_clinician": [],
        }
    )


def _stored(h: Any) -> list[dict[str, Any]]:
    row = h.admin.execute(
        "select availability_evidence from app.page_analysis where case_id = %s", (h.case_id,)
    ).fetchone()
    assert row is not None
    return list(row[0])


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
async def test_evidence_is_checked_and_its_date_read_in_the_packs_date_order(
    combo: Combo,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> None:
    order = default_library().combine(*combo.codes).date_order
    evidence = [
        {"kind": "received", "date": "2024-05-22", "word_ids": ["p1w1", "p1w2"]},
        {"kind": "filed", "date": None, "word_ids": ["p1w3", "p1w4"]},  # two-digit year: ambiguous
        {"kind": "seen", "date": None, "word_ids": ["p1w5", "p1w6"]},
        {"kind": "sent_to_clinician", "date": None, "word_ids": ["p1w99"]},  # not a word on the page
        {"kind": "seen", "date": "2030-01-01", "word_ids": ["p1w3"]},  # a date the cited words do not print
    ]
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    opened = harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=None,
        bedrock=ScriptedBedrock([_answer(evidence)]),
        names=[],
        dates_of_birth=[],
    )
    async with opened as h:
        await h.upload(text_pdf([[f"Received {PRINTED[order]} Filed 03/04/24 Seen 03/04/2024"]]))
        finished = await h.drain()
        assert set(finished.values()) == {"done"}
        stored = _stored(h)
        assert [(e["kind"], e["date"], e["word_ids"]) for e in stored] == [
            ("received", "2024-05-22", ["p1w1", "p1w2"]),
            ("filed", None, ["p1w3", "p1w4"]),
            ("seen", READ_AS[order].isoformat(), ["p1w5", "p1w6"]),
        ]
        assert stored[0]["date_text"] == PRINTED[order]
        assert stored[1]["date_text"] is None
