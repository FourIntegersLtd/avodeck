"""Slice A: repair_fact_dates job fills event_date from printed words (F12)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from app.features.pipeline import date_repair, dates
from tests.repository.test_pipeline_support import harness
from tests.support.db import scope_for
from tests.support.facts_seed import insert_fact
from tests.support.files import local_storage
from tests.support.packs import BOTH, FIXTURE, LAUNCH, Combo
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
        from app.features.pipeline.paths import word_purpose

        async with api_db.request(scope_for(expert)) as conn:
            for n, word_id, text in ((1, "w1", "03/04/2024"), (2, "w2", "review")):
                sealed = await h.crypto.encrypt(conn, case_id, text.encode(), purpose=word_purpose(page_id, word_id))
                admin.execute(
                    "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
                    " values (%s, %s, %s, %s, %s, %s, 0.9, 'ocr')",
                    (page_id, case_id, n, word_id, sealed, [0, 0, 1, 0, 1, 1, 0, 1]),
                )
        resp = await h.client.post(f"/v1/cases/{case_id}/repair-fact-dates")
        assert resp.status_code == 202
        await h.drain()
        row = admin.execute("select event_date, date_basis from app.facts where id = %s", (fact_id,)).fetchone()
        assert row == (dt.date(2024, 4, 3), "printed")


# ---- century rule and the line above (pure) -------------------------------------------------


def _written(day: int, month: int, year: int, order: str, *, short: bool) -> str:
    yy = f"{year % 100:02d}" if short else str(year)
    first, second = (day, month) if order == "DMY" else (month, day)
    return f"{first:02d}/{second:02d}/{yy}"


def _page(*lines: str) -> tuple[list[tuple[str, str]], dict[str, int]]:
    ordered: list[tuple[str, str]] = []
    line_of: dict[str, int] = {}
    for number, line in enumerate(lines):
        for token in line.split():
            word_id = f"w{len(ordered) + 1}"
            ordered.append((word_id, token))
            line_of[word_id] = number
    return ordered, line_of


def _above(pack, ordered, line_of, cite, fields=None, anchor=dt.date(2000, 1, 1)):  # type: ignore[no-untyped-def]
    return date_repair.line_above_date("result", fields or {}, cite, ordered, set(), line_of, pack, anchor=anchor)


@pytest.fixture
def launch_pack():  # type: ignore[no-untyped-def]
    return default_library().combine(*LAUNCH.codes)


def test_a_two_digit_year_gets_a_century_when_allowed_and_not_otherwise(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, _ = _page("03/04/87 review")
    kind = launch_pack.fact_kinds()[0]["key"]
    off = date_repair.resolve_fact_event_date(kind, {}, ["w1"], ordered, set(), launch_pack)
    on = date_repair.resolve_fact_event_date(
        kind, {}, ["w1"], ordered, set(), launch_pack, allow_century=True, anchor=dt.date(2000, 1, 1)
    )
    assert off == (None, None)
    assert on == (dt.date(1987, 4, 3), dates.PRINTED_CENTURY)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
def test_the_century_rule_follows_the_packs_date_order(combo: Combo) -> None:
    pack = default_library().combine(*combo.codes)
    ordered, _ = _page(_written(3, 4, 1987, pack.date_order, short=True) + " review")
    got = date_repair.resolve_fact_event_date(
        "x", {}, ["w1"], ordered, set(), pack, allow_century=True, anchor=dt.date(2000, 1, 1)
    )
    assert got == (dt.date(1987, 4, 3), dates.PRINTED_CENTURY)


def test_a_four_digit_date_keeps_the_printed_basis(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, _ = _page("03/04/1987 review")
    got = date_repair.resolve_fact_event_date(
        "x", {}, ["w1"], ordered, set(), launch_pack, allow_century=True, anchor=dt.date(2000, 1, 1)
    )
    assert got == (dt.date(1987, 4, 3), dates.PRINTED)


def test_an_unreadable_date_stays_empty(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, _ = _page("181.88 review")
    got = date_repair.resolve_fact_event_date(
        "x", {}, ["w1"], ordered, set(), launch_pack, allow_century=True, anchor=dt.date(2000, 1, 1)
    )
    assert got == (None, None)


def test_a_result_mid_line_takes_the_date_at_the_start_of_its_line(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, line_of = _page("21/10/87 FBC nad Hb 112 g/L")
    when, basis, ids = _above(launch_pack, ordered, line_of, ["w5", "w6", "w7"])
    assert (when, basis) == (dt.date(1987, 10, 21), dates.LINE_ABOVE)
    assert ids == ["w1"]


def test_a_result_takes_the_date_from_the_line_immediately_above(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, line_of = _page("21/10/1987 FBC", "Hb 112 g/L")
    assert _above(launch_pack, ordered, line_of, ["w4", "w5"]) == (dt.date(1987, 10, 21), dates.LINE_ABOVE, ["w1"])


def test_a_date_two_lines_up_is_too_far(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, line_of = _page("21/10/1987 FBC", "nad", "Hb 112 g/L")
    assert _above(launch_pack, ordered, line_of, ["w5", "w6"]) == (None, None, [])


def test_a_date_below_is_never_used(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, line_of = _page("Hb 112 g/L", "21/10/1987 FBC")
    assert _above(launch_pack, ordered, line_of, ["w1", "w2"]) == (None, None, [])


def test_no_date_on_the_page_leaves_the_fact_undated(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, line_of = _page("Hb 112 g/L")
    assert _above(launch_pack, ordered, line_of, ["w1"]) == (None, None, [])


def test_the_nearest_date_above_wins(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, line_of = _page("01/01/1987 old", "21/10/1987 FBC Hb 112")
    assert _above(launch_pack, ordered, line_of, ["w6", "w7"])[0] == dt.date(1987, 10, 21)


def test_a_two_digit_date_above_gets_the_century_rule(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, line_of = _page("21/10/87 FBC Hb 112")
    assert _above(launch_pack, ordered, line_of, ["w4", "w5"])[:2] == (dt.date(1987, 10, 21), dates.LINE_ABOVE)


def test_a_fact_with_a_pointer_is_not_given_a_date_from_the_line(launch_pack) -> None:  # type: ignore[no-untyped-def]
    ordered, line_of = _page("21/10/1987 FBC Hb 112")
    field = dates.date_field(launch_pack, "result")
    assert field is not None
    assert _above(launch_pack, ordered, line_of, ["w4", "w5"], {field: "05/05/1990"}) == (None, None, [])


def test_a_kind_without_a_date_field_is_not_given_one() -> None:
    pack = default_library().combine(*FIXTURE.codes)
    ordered, line_of = _page("10/21/1987 FBC Hb 112")
    got = date_repair.line_above_date("measurement", {}, ["w4"], ordered, set(), line_of, pack)
    assert got == (None, None, [])


def test_the_line_above_reads_in_the_packs_date_order(monkeypatch: pytest.MonkeyPatch) -> None:
    pack = default_library().combine(*FIXTURE.codes)
    monkeypatch.setattr(dates, "date_field", lambda _p, _k: "when")
    ordered, line_of = _page("10/21/87 FBC Hb 112")
    got = date_repair.line_above_date(
        "measurement", {}, ["w4"], ordered, set(), line_of, pack, anchor=dt.date(2000, 1, 1)
    )
    assert got == (dt.date(1987, 10, 21), dates.LINE_ABOVE, ["w1"])


def test_lines_are_told_apart_by_their_height_on_the_page() -> None:
    def box(y: float) -> list[float]:
        return [0.0, y, 1.0, y, 1.0, y + 10, 0.0, y + 10]

    got = dates.line_numbers([("a", box(100)), ("b", box(101)), ("c", box(130)), ("d", [])])
    assert got["a"] == got["b"]
    assert got["c"] == got["a"] + 1
    assert got["d"] not in (got["a"], got["c"], got["c"] + 1)  # an unreadable box is never "the same line"


async def _store_words(h, admin, api_db, expert, case_id, page_id, rows) -> None:  # type: ignore[no-untyped-def]
    from app.features.pipeline.paths import word_purpose

    async with api_db.request(scope_for(expert)) as conn:
        for n, (word_id, text, y) in enumerate(rows, start=1):
            sealed = await h.crypto.encrypt(conn, case_id, text.encode(), purpose=word_purpose(page_id, word_id))
            admin.execute(
                "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
                " values (%s, %s, %s, %s, %s, %s, 0.9, 'ocr')",
                (page_id, case_id, n, word_id, sealed, [0, y, 1, y, 1, y + 10, 0, y + 10]),
            )


async def test_repair_job_reads_century_and_line_above_for_suggested_facts(
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
    pack = default_library().combine(*LAUNCH.codes)
    kind = next(k["key"] for k in pack.fact_kinds() if dates.date_field(pack, k["key"]))
    field = dates.date_field(pack, kind)
    assert field is not None
    rows = [
        ("w1", "03/04/87", 0),
        ("w2", "review", 0),
        ("w3", "12/05/1990", 100),
        ("w4", "seen", 100),
        ("w5", "181.88", 200),
        ("w6", "other", 200),
        ("w7", "Hb", 300),
        ("w8", "112", 300),
    ]

    def fact(fields: dict[str, str], value: str, word: str) -> uuid.UUID:
        return insert_fact(
            admin, case_id, page_id, kind, fields=fields, value=value, word_ids=[word], status="suggested"
        )

    short = fact({field: "03/04/87"}, "a", "w1")
    full = fact({field: "12/05/1990"}, "b", "w3")
    bad = fact({field: "181.88"}, "c", "w5")
    mid = fact({}, "d", "w8")
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
        await _store_words(h, admin, api_db, expert, case_id, page_id, rows)
        resp = await h.client.post(f"/v1/cases/{case_id}/repair-fact-dates")
        assert resp.status_code == 202
        await h.drain()
        got = {
            fid: admin.execute("select event_date, date_basis from app.facts where id = %s", (fid,)).fetchone()
            for fid in (short, full, bad, mid)
        }
    assert got[short] == (dt.date(1987, 4, 3), "printed_century")
    assert got[full] == (dt.date(1990, 5, 12), "printed")
    assert got[bad] == (None, None)
    assert got[mid] == (None, None)  # the line above it holds no readable date
