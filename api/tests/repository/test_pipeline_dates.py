"""Slice A increment 4: each fact's date, read by code in the case's date order (architecture 6.4, F12).

The model points at the printed date that belongs to a fact; code reads that date from the page's
words with the pack's date order. A fact whose date is not printed, or is printed with a two-digit
year, is marked "Please check" (`date_basis = 'please_check'`) with no date: never guessed. Each
page's printed dates are kept for sorting. Runs on the committed 18-page subset under the launch
packs, and on a typed page under both pack combinations; messages name pages only.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import (
    Harness,
    date_score,
    expected,
    expected_dates,
    facts_of,
    harness,
    recorded_book,
    rekeyed_store,
    subset_document,
    subset_page_bytes,
    text_pdf,
)
from tests.support.ai import ScriptedBedrock, response
from tests.support.files import local_storage
from tests.support.packs import BOTH, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

READ_AS = {"DMY": dt.date(2024, 4, 3), "MDY": dt.date(2024, 3, 4)}  # 03/04/2024 under each date order


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


async def _open(
    combo: Combo,
    store: FixtureStore | None,
    bedrock: Any,
    *,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
    names: list[str] | None = None,
    dates_of_birth: list[dt.date] | None = None,
) -> Any:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    return harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=store,
        bedrock=bedrock,
        names=names or [],
        dates_of_birth=dates_of_birth or [],
    )


@pytest.fixture
async def subset(
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> AsyncIterator[Harness]:
    recordings = rekeyed_store(tmp_path / "recordings", subset_document(), subset_page_bytes())
    case = expected("case.json")
    opened = await _open(
        LAUNCH,
        recordings,
        recorded_book(recordings),
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
        names=list(case["case_names"]),
        dates_of_birth=[dt.date.fromisoformat(case["dob"])],
    )
    async with opened as h:
        await h.upload(subset_document())
        finished = await h.drain(timeout=300)
        assert set(finished.values()) == {"done"}, finished
        yield h


def subset_facts(h: Harness) -> list[tuple[int, dict[str, Any]]]:
    """The facts keyed by their page's number in the full bundle (the subset keeps the bundle's order)."""
    from tests.repository.test_pipeline_support import subset_numbers

    numbers = dict(enumerate(subset_numbers(), start=1))
    return [(numbers[f["bundle_page_no"]], f) for f in facts_of(h)]


def printed_dates_of(h: Harness, page_id: uuid.UUID) -> list[dt.date]:
    row = h.admin.execute("select printed_dates from app.page_analysis where page_id = %s", (page_id,)).fetchone()
    assert row is not None
    return list(row[0] or [])


async def test_printed_dates_are_read_as_a_person_reads_them(subset: Harness) -> None:
    score = date_score((n, f["value"], f["event_date"], f["date_basis"]) for n, f in subset_facts(subset))
    assert score.wanted >= 15, "too few dated facts were found to judge"
    assert score.printed >= 5, f"only {score.printed} facts were given a printed date"
    assert score.accuracy >= 0.9, f"printed dates right on {score.accuracy:.3f} of facts"


async def test_a_dated_fact_is_given_its_date_or_marked_please_check(subset: Harness) -> None:
    want = expected_dates()
    for n, fact in subset_facts(subset):
        basis, when = fact["date_basis"], fact["event_date"]
        assert basis in (None, "printed", "please_check"), f"page {n}"
        assert (when is not None) == (basis == "printed"), f"page {n}: a date without a printed basis"
        if want.get((n, fact["value"])):
            assert basis is not None, f"page {n}: a dated fact has neither a date nor a flag"


async def test_a_two_digit_year_is_never_guessed(subset: Harness) -> None:
    from app.features.pipeline import dates
    from tests.repository.test_pipeline_support import page_words

    pack = default_library().combine(*LAUNCH.codes)
    pages = {p["bundle_page_no"]: p for p in subset.pages()}
    for n, fact in subset_facts(subset):
        if fact["date_basis"] != "printed":
            continue
        words = await page_words(subset, pages[fact["bundle_page_no"]]["id"])
        found = dates.find([(w, t) for w, (t, _) in words.items()], pack)
        readable = [d for d in found if not d.ambiguous and d.value == fact["event_date"]]
        assert readable, f"page {n}: a date was given that is not printed in full on the page"


async def test_each_page_keeps_its_printed_dates(subset: Harness) -> None:
    pages = {p["bundle_page_no"]: p for p in subset.pages()}
    for n, fact in subset_facts(subset):
        if fact["date_basis"] == "printed":
            assert fact["event_date"] in printed_dates_of(subset, pages[fact["bundle_page_no"]]["id"]), f"page {n}"
    for page in pages.values():
        kept = printed_dates_of(subset, page["id"])
        assert kept == sorted(set(kept)), f"page {page['bundle_page_no']}"


def _dated_answer(combo: Combo) -> Any:
    """A T-07b answer with one fact on the word "review" (p1w3), dated 2024-04-03 where the pack's
    kind has a date field."""
    pack = default_library().combine(*combo.codes)
    kinds = pack.fact_kinds()
    kind = next((k for k in kinds if "date" in k.get("fields", {}).values()), kinds[0])
    fields = {name: ("2024-04-03" if t == "date" else None) for name, t in kind.get("fields", {}).items()}
    fields = {name: value for name, value in fields.items() if value is not None}
    return response(
        {
            "source_type": pack.source_types()[0]["key"],
            "printed_dates": ["2024-04-03"],
            "facts": [
                {"kind": kind["key"], "clinician_role": None, "fields": fields, "value": "review", "word_ids": ["p1w3"]}
            ],
            "references_out": [],
            "availability_evidence": [],
            "actions_requested_of_clinician": [],
        }
    )


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
async def test_the_same_printed_date_reads_in_each_pack_date_order(
    combo: Combo,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> None:
    from app.features.pipeline import dates

    pack = default_library().combine(*combo.codes)
    opened = await _open(
        combo,
        None,
        ScriptedBedrock([_dated_answer(combo)]),
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    )
    async with opened as h:
        await h.upload(text_pdf([["Seen 03/04/2024 review"], ["Seen 03/04/24 review"]]))
        finished = await h.drain()
        assert set(finished.values()) == {"done"}
        full, short = sorted(h.pages(), key=lambda p: p["bundle_page_no"])
        assert printed_dates_of(h, full["id"]) == [READ_AS[pack.date_order]]
        assert printed_dates_of(h, short["id"]) == []
        facts = {f["bundle_page_no"]: f for f in facts_of(h)}
        assert set(facts) == {full["bundle_page_no"], short["bundle_page_no"]}
        kind = facts[full["bundle_page_no"]]["kind"]
        if dates.date_field(pack, kind) is None:
            for fact in facts.values():
                assert (fact["event_date"], fact["date_basis"]) == (None, None)
        else:
            first, second = facts[full["bundle_page_no"]], facts[short["bundle_page_no"]]
            assert (first["event_date"], first["date_basis"]) == (READ_AS[pack.date_order], "printed")
            assert (second["event_date"], second["date_basis"]) == (None, "please_check")
