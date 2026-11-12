"""Security audit 16 (document provenance and output integrity), slice B findings.

Each test asserts the SAFE outcome and carries its finding id.

- PV-01: the database refuses a fact citing words that are not words of its own page (insert or change).
- PV-02: a checked fact's value, details or cited words change only together with a `fact_verifications` row.
- PV-03: the check log keeps the old and new date and how it was read, inside the row hash; a date the expert
  types is not labelled as read from the page.
- PV-12 (reader parts): a typed transcription, relevance / clearer copy, a turn or rotation and the removal of
  a flag each leave who and when (and the earlier value where it is evidence).

Everything here is made up; nothing resembles a record, and no text goes to a log line (rule 6).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any, cast

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.contracts.pipeline import (
    extract_page_text_entry,
    process_pages_entry,
    transcription_purpose,
)
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.http import etag
from app.core.storage import CaseFileStorage
from tests.fixtures.stub_facts import load_stub_facts
from tests.repository.test_pipeline_reread import add_fact, fact, h, page_ids, type_out, words_for  # noqa: F401
from tests.repository.test_pipeline_support import Harness
from tests.support.facts_seed import insert_fact
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
REFUSED = "AV409"


# --- the database world (restricted roles) ------------------------------------------------------------------------


@pytest.fixture
def db_world(seed: Seed, admin: psycopg.Connection) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    doc = seed.document(case)
    page, typed_page, other_page = (seed.page(case, doc, n) for n in (1, 2, 3))
    bundle = admin.execute("select bundle_id from app.cases where id = %s", (case,)).fetchone()
    assert bundle is not None

    def words(page_id: uuid.UUID, ids: list[str], source: str) -> None:
        for n, word_id in enumerate(ids, start=1):
            admin.execute(
                "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
                " values (%s, %s, %s, %s, %s, %s::real[], 0.99, %s)",
                (page_id, bundle[0], n, word_id, b"x", [0, 0, 1, 0, 1, 1, 0, 1], source),
            )

    words(page, ["w1", "w2", "w3", "w4"], "ocr")
    words(typed_page, ["e1", "e2"], "expert")
    words(other_page, ["q1", "q2"], "ocr")
    return {"case": case, "expert": expert, "page": page, "typed_page": typed_page, "other_page": other_page}


def _fact(admin: psycopg.Connection, w: dict[str, Any], status: str) -> uuid.UUID:
    return insert_fact(admin, w["case"], w["page"], "zz_result", {"k": "v"}, "val", ["w1", "w2"], status=status)


@pytest.fixture
def api_conn(api_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(api_dsn, connect_timeout=20) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def worker_conn(worker_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(worker_dsn, connect_timeout=20) as conn:
        yield conn
        conn.rollback()


def as_expert(conn: psycopg.Connection, w: dict[str, Any]) -> None:
    conn.execute("select set_config('app.person_id', %s, true)", (str(w["expert"]),))


def as_job(conn: psycopg.Connection, w: dict[str, Any]) -> None:
    conn.execute("select set_config('app.job_case_id', %s, true)", (str(w["case"]),))


def attempt(conn: psycopg.Connection, *statements: tuple[str, tuple[Any, ...]]) -> str | None:
    """Run the statements in one savepoint, then make the deferred checks run now (they otherwise run at commit).
    The SQLSTATE of the refusal, or None when everything was accepted."""
    try:
        with conn.transaction():
            # an earlier accepted attempt left the checks immediate for the rest of the transaction
            conn.execute("set constraints all deferred")
            for query, params in statements:
                conn.execute(query, params)
            conn.execute("set constraints all immediate")
        return None
    except psycopg.Error as exc:
        return exc.sqlstate or "?"


def new_fact(w: dict[str, Any], page: str, word_ids: list[str]) -> tuple[str, tuple[Any, ...]]:
    return (
        "insert into app.facts (case_id, page_id, kind, value, word_ids) values (%s, %s, 'zz_result', 'val', %s)",
        (w["case"], w[page], word_ids),
    )


OLD_FIELDS = '{"k": "v"}'
OLD_WORDS = ["w1", "w2"]


def log_row(
    w: dict[str, Any],
    fact_id: uuid.UUID,
    person: bool = True,
    *,
    old_value: str | None = "val",
    old_fields: str | None = OLD_FIELDS,
    old_words: list[str] | None = OLD_WORDS,
    old_date: str | None = None,
    old_basis: str | None = None,
    action: str | None = None,
) -> tuple[str, tuple[Any, ...]]:
    """The log row a check writes: the fact as it now stands is the row's new value, details, words and date, and
    the row carries what it was before (PV-02b: the earlier value survives in the log). The defaults are the
    fact `_fact` seeds, so a row that leaves them alone says "the fact was as seeded"."""
    return (
        "insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value,"
        " old_fields, new_fields, old_word_ids, new_word_ids, old_event_date, new_event_date, old_date_basis,"
        " new_date_basis) select f.case_id, f.id, %s, %s, %s, f.value, %s::jsonb, f.fields, %s::text[], f.word_ids,"
        " %s::date, f.event_date, %s, f.date_basis from app.facts f where f.id = %s",
        (
            w["expert"] if person else None,
            action or ("edit" if person else "remapped"),
            old_value,
            old_fields,
            old_words,
            old_date,
            old_basis,
            fact_id,
        ),
    )


# PV-01 -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("words", [["zz99"], ["w1", "zz99"], ["q1"]], ids=["unknown", "partly unknown", "other page"])
def test_pv01_the_worker_cannot_store_a_fact_citing_words_that_are_not_on_its_page(
    worker_conn: psycopg.Connection, db_world: dict[str, Any], words: list[str]
) -> None:
    as_job(worker_conn, db_world)
    assert attempt(worker_conn, new_fact(db_world, "page", words)) == REFUSED


def test_pv01_the_api_role_cannot_store_a_fact_citing_words_that_are_not_on_its_page(
    api_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    as_expert(api_conn, db_world)
    assert attempt(api_conn, new_fact(db_world, "page", ["zz99"])) == REFUSED


@pytest.mark.parametrize("page,words", [("page", ["w3", "w4"]), ("typed_page", ["e1", "e2"])])
def test_pv01_control_a_fact_citing_its_own_pages_words_is_stored(
    worker_conn: psycopg.Connection, db_world: dict[str, Any], page: str, words: list[str]
) -> None:
    as_job(worker_conn, db_world)
    assert attempt(worker_conn, new_fact(db_world, page, words)) is None


def test_pv01_a_checked_fact_cannot_be_pointed_at_words_that_are_not_on_its_page_even_with_a_log_row(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    fact_id = _fact(admin, db_world, "verified")
    as_expert(api_conn, db_world)
    out = attempt(
        api_conn,
        ("update app.facts set word_ids = %s where id = %s", (["zz99"], fact_id)),
        log_row(db_world, fact_id),
    )
    assert out == REFUSED


def test_pv01_control_the_same_change_to_real_words_with_a_log_row_is_accepted(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    fact_id = _fact(admin, db_world, "verified")
    as_expert(api_conn, db_world)
    out = attempt(
        api_conn,
        ("update app.facts set word_ids = %s where id = %s", (["w3", "w4"], fact_id)),
        log_row(db_world, fact_id),
    )
    assert out is None


# PV-02 -----------------------------------------------------------------------------------------------------------

CHANGES = {
    "value": ("update app.facts set value = 'other' where id = %s", ()),
    "fields": ('update app.facts set fields = \'{"k": "other"}\' where id = %s', ()),
    "word_ids": ("update app.facts set word_ids = array['w3', 'w4'] where id = %s", ()),
}


@pytest.mark.parametrize("status", ["verified", "edited"])
@pytest.mark.parametrize("what", sorted(CHANGES))
def test_pv02_a_checked_fact_cannot_change_without_a_log_row(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any], what: str, status: str
) -> None:
    fact_id = _fact(admin, db_world, status)
    as_expert(api_conn, db_world)
    query, _ = CHANGES[what]
    assert attempt(api_conn, (query, (fact_id,))) == REFUSED


@pytest.mark.parametrize("what", sorted(CHANGES))
def test_pv02_control_a_checked_fact_changes_with_a_log_row_in_the_same_transaction(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any], what: str
) -> None:
    fact_id = _fact(admin, db_world, "verified")
    as_expert(api_conn, db_world)
    query, _ = CHANGES[what]
    assert attempt(api_conn, (query, (fact_id,)), log_row(db_world, fact_id)) is None


def test_pv02_a_log_row_of_another_fact_does_not_cover_the_change(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    fact_id, other = _fact(admin, db_world, "verified"), _fact(admin, db_world, "verified")
    as_expert(api_conn, db_world)
    query, _ = CHANGES["value"]
    assert attempt(api_conn, (query, (fact_id,)), log_row(db_world, other)) == REFUSED


def test_pv02_the_re_read_re_map_still_works_when_it_logs_a_remapped_row(
    admin: psycopg.Connection, worker_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    fact_id = _fact(admin, db_world, "verified")
    as_job(worker_conn, db_world)
    move = ("update app.facts set word_ids = array['w3', 'w4'] where id = %s", (fact_id,))
    assert attempt(worker_conn, move) == REFUSED
    assert attempt(worker_conn, move, log_row(db_world, fact_id, person=False)) is None


STATUS_MOVES = [
    ("suggested", "verified"),
    ("suggested", "edited"),
    ("verified", "rejected"),
    ("edited", "rejected"),
    ("rejected", "verified"),
]


def _move(
    admin: psycopg.Connection, w: dict[str, Any], start: str, end: str
) -> tuple[uuid.UUID, tuple[str, tuple[Any, ...]]]:
    fact_id = _fact(admin, w, start)
    return fact_id, ("update app.facts set status = %s where id = %s", (end, fact_id))


@pytest.mark.parametrize(("start", "end"), STATUS_MOVES)
def test_pv02_a_status_change_into_or_out_of_checked_needs_a_log_row(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any], start: str, end: str
) -> None:
    _, move = _move(admin, db_world, start, end)
    as_expert(api_conn, db_world)
    assert attempt(api_conn, move) == REFUSED


@pytest.mark.parametrize(("start", "end"), STATUS_MOVES)
def test_pv02_control_a_status_change_with_its_log_row_in_the_same_transaction_is_accepted(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any], start: str, end: str
) -> None:
    fact_id, move = _move(admin, db_world, start, end)
    as_expert(api_conn, db_world)
    assert attempt(api_conn, move, log_row(db_world, fact_id, action="correct")) is None


def test_pv02_a_status_change_of_another_fact_is_not_covered_by_this_facts_log_row(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    fact_id, move = _move(admin, db_world, "suggested", "verified")
    other = _fact(admin, db_world, "suggested")
    as_expert(api_conn, db_world)
    assert attempt(api_conn, move, log_row(db_world, other, action="correct")) == REFUSED


def test_pv02_control_a_change_between_two_unchecked_states_needs_no_log_row(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    fact_id = _fact(admin, db_world, "suggested")
    as_expert(api_conn, db_world)
    assert attempt(api_conn, ("update app.facts set value = 'other' where id = %s", (fact_id,))) is None
    assert attempt(api_conn, ("update app.facts set status = 'rejected' where id = %s", (fact_id,))) is None


def test_pv02_a_value_changed_while_suggested_cannot_be_re_verified_under_the_old_value(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    """The reviewer's path: a checked value is undone to suggested (transaction 1, by the reset), changed while
    suggested (transaction 2), checked again (transaction 3). The log row of the check must carry the value the
    fact had when it was checked, so the earlier value cannot be lost: a row that says the fact still had the old
    value is refused, a true one is accepted."""
    fact_id = _fact(admin, db_world, "suggested")
    as_expert(api_conn, db_world)
    assert attempt(api_conn, ("update app.facts set value = 'changed' where id = %s", (fact_id,))) is None
    verify = ("update app.facts set status = 'verified' where id = %s", (fact_id,))
    assert attempt(api_conn, verify, log_row(db_world, fact_id, action="correct")) == REFUSED
    assert attempt(api_conn, verify, log_row(db_world, fact_id, action="correct", old_value="changed")) is None


LOST: dict[str, tuple[str, dict[str, Any]]] = {
    "value": ("value", {"old_value": None}),
    "value wrong": ("value", {"old_value": "other"}),
    "fields": ("fields", {"old_fields": None}),
    "fields wrong": ("fields", {"old_fields": '{"k": "x"}'}),
    "word_ids": ("word_ids", {"old_words": None}),
    "word_ids wrong": ("word_ids", {"old_words": ["w3"]}),
}


@pytest.mark.parametrize("case", sorted(LOST))
def test_pv02b_the_log_row_must_carry_the_value_details_and_words_the_fact_had_before(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any], case: str
) -> None:
    what, bad = LOST[case]
    fact_id = _fact(admin, db_world, "verified")
    as_expert(api_conn, db_world)
    query, _ = CHANGES[what]
    assert attempt(api_conn, (query, (fact_id,)), log_row(db_world, fact_id, **bad)) == REFUSED


# PV-03 (the date): a date change on a checked fact is a change like any other.
DATE_CHANGES = {
    "date": "update app.facts set event_date = '2020-02-02' where id = %s",
    "basis": "update app.facts set date_basis = 'printed' where id = %s",
    "both": "update app.facts set event_date = '2020-02-02', date_basis = 'printed' where id = %s",
}


@pytest.mark.parametrize("status", ["verified", "edited"])
@pytest.mark.parametrize("what", sorted(DATE_CHANGES))
def test_pv03_a_date_only_change_on_a_checked_fact_needs_a_log_row(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any], what: str, status: str
) -> None:
    fact_id = _fact(admin, db_world, status)
    as_expert(api_conn, db_world)
    assert attempt(api_conn, (DATE_CHANGES[what], (fact_id,))) == REFUSED


@pytest.mark.parametrize("what", sorted(DATE_CHANGES))
def test_pv03_control_a_date_change_with_a_log_row_carrying_both_dates_is_accepted(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any], what: str
) -> None:
    fact_id = _fact(admin, db_world, "verified")
    as_expert(api_conn, db_world)
    assert attempt(api_conn, (DATE_CHANGES[what], (fact_id,)), log_row(db_world, fact_id)) is None


@pytest.mark.parametrize("bad", [{"old_date": "2019-01-01"}, {"old_basis": "assumed_century"}])
def test_pv03_the_log_row_must_carry_the_date_the_fact_had_before(
    admin: psycopg.Connection, api_conn: psycopg.Connection, db_world: dict[str, Any], bad: dict[str, Any]
) -> None:
    fact_id = _fact(admin, db_world, "verified")
    admin.execute("update app.facts set event_date = '2020-01-01', date_basis = 'printed' where id = %s", (fact_id,))
    as_expert(api_conn, db_world)
    change = ("update app.facts set event_date = '2020-02-02', date_basis = 'expert_entered' where id = %s", (fact_id,))
    good: dict[str, Any] = {"old_date": "2020-01-01", "old_basis": "printed"}
    assert attempt(api_conn, change, log_row(db_world, fact_id, **{**good, **bad})) == REFUSED
    assert attempt(api_conn, change, log_row(db_world, fact_id, **good)) is None


def test_pv03_control_the_pipelines_date_repair_row_covers_words_and_date_together(
    admin: psycopg.Connection, worker_conn: psycopg.Connection, db_world: dict[str, Any]
) -> None:
    """A checked fact dated by the job: words widened to take in the date's words, with its `remapped` row."""
    fact_id = _fact(admin, db_world, "verified")
    as_job(worker_conn, db_world)
    repair = (
        "update app.facts set event_date = '2020-02-02', date_basis = 'printed',"
        " word_ids = array['w1', 'w2', 'w3'] where id = %s",
        (fact_id,),
    )
    assert attempt(worker_conn, repair) == REFUSED
    assert attempt(worker_conn, repair, log_row(db_world, fact_id, person=False)) is None


# --- the HTTP worlds ---------------------------------------------------------------------------------------------


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    from tests.support.files import local_storage

    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage) -> FastAPI:
    from tests.support.files import build_app

    return build_app(api_db, auth_env, crypto=CRYPTO, storage=storage)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def facts_world(seed: Seed, aconn: psycopg.AsyncConnection, client_for: Any) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(
        seed.org(kind="personal", members={expert: "owner"}), expert, status="active", packs=LAUNCH.case_row()
    )
    loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO)
    return {"case": case, "expert": expert, "page_id": loaded[118].page_id, "client": await client_for(expert)}


async def checked_fact(
    admin: psycopg.Connection, w: dict[str, Any], when: str | None, basis: str | None
) -> dict[str, Any]:
    """The first fact of page 118, given this date and basis, read by the expert, as the API shows it."""
    admin.execute(
        "update app.facts set event_date = %s, date_basis = %s where id = (select id from app.facts where page_id = %s"
        " and value is not null order by id limit 1)",
        (when, basis, w["page_id"]),
    )
    client: httpx.AsyncClient = w["client"]
    base = f"/v1/cases/{w['case']}"
    assert (await client.post(f"{base}/pages/118/read", json={"dwell_seconds": 20})).status_code == 200
    listed = (await client.get(f"{base}/pages/118/facts")).json()["facts"]
    row = admin.execute(
        "select id from app.facts where page_id = %s and value is not null order by id limit 1", (w["page_id"],)
    ).fetchone()
    assert row is not None
    return next(f for f in listed if f["id"] == str(row[0]))


async def edit(w: dict[str, Any], fact: dict[str, Any], **body: Any) -> httpx.Response:
    response = await w["client"].post(
        f"/v1/cases/{w['case']}/facts/{fact['id']}/verify",
        json={"action": "edit", **body},
        headers={"if-match": etag(fact["version"])},
    )
    return cast(httpx.Response, response)


def date_row(admin: psycopg.Connection, fact_id: str) -> tuple[Any, ...]:
    row = admin.execute("select event_date, date_basis from app.facts where id = %s", (fact_id,)).fetchone()
    assert row is not None
    return row


# PV-03 -----------------------------------------------------------------------------------------------------------


async def test_pv03a_a_date_only_edit_keeps_the_old_and_new_date_and_basis_in_the_log(
    admin: psycopg.Connection, facts_world: dict[str, Any]
) -> None:
    fact = await checked_fact(admin, facts_world, "2020-01-02", "printed")
    assert (await edit(facts_world, fact, event_date="2021-03-04")).status_code == 200
    row = admin.execute(
        "select old_event_date::text, new_event_date::text, old_date_basis, new_date_basis"
        " from app.fact_verifications where fact_id = %s order by seq desc limit 1",
        (fact["id"],),
    ).fetchone()
    assert row == ("2020-01-02", "2021-03-04", "printed", "expert_entered")


async def test_pv03a_the_dates_are_inside_the_row_hash_and_old_rows_still_verify(
    admin: psycopg.Connection, facts_world: dict[str, Any]
) -> None:
    case = facts_world["case"]
    fact = await checked_fact(admin, facts_world, "2020-01-02", "printed")
    legacy = admin.execute(  # a row written before the date columns existed: no dates at all
        "insert into app.fact_verifications (case_id, fact_id, person_id, action, old_value, new_value)"
        " values (%s, %s, %s, 'correct', 'a', 'a') returning id",
        (case, fact["id"], facts_world["expert"]),
    ).fetchone()
    assert legacy is not None
    assert (await edit(facts_world, fact, event_date="2021-03-04")).status_code == 200
    ok = admin.execute("select app.fact_verifications_chain_ok(%s)", (case,)).fetchone()
    assert ok == (True,)
    # The legacy row's hash is the one the original formula gives (nothing appended for empty dates).
    same = admin.execute(
        """select r.hash = encode(sha256(convert_to(concat_ws(chr(31),
            r.id::text, r.case_id::text, r.fact_id::text, coalesce(r.person_id::text, ''), r.seq::text, r.action,
            coalesce(r.old_value, ''), coalesce(r.new_value, ''), coalesce(r.old_fields::text, ''),
            coalesce(r.new_fields::text, ''), coalesce(r.old_word_ids::text, ''), coalesce(r.new_word_ids::text, ''),
            coalesce(r.old_page_id::text, ''), coalesce(r.new_page_id::text, ''), coalesce(r.reason, ''),
            ((extract(epoch from r.at) * 1000000)::bigint)::text, r.prev_hash), 'UTF8')), 'hex')
           from app.fact_verifications r where r.id = %s""",
        (legacy[0],),
    ).fetchone()
    assert same == (True,)
    covered = admin.execute(
        """select app.fact_verifications_hash(
                    jsonb_populate_record(r, jsonb_build_object('new_event_date', '1999-01-01')))
                  <> r.hash
           from app.fact_verifications r where r.fact_id = %s order by r.seq desc limit 1""",
        (fact["id"],),
    ).fetchone()
    assert covered == (True,)


async def test_pv03a_rows_with_no_person_or_with_a_reading_still_hash_as_they_did_before_the_date_columns(
    admin: psycopg.Connection, facts_world: dict[str, Any]
) -> None:
    """A remapped row has no person and a correction row names a reading; both hashed with those parts in 0211."""
    case = facts_world["case"]
    fact = await checked_fact(admin, facts_world, "2020-01-02", "printed")
    ids = [
        admin.execute(
            "insert into app.fact_verifications"
            " (case_id, fact_id, person_id, action, old_value, new_value, correction_id)"
            " values (%s, %s, %s, %s, 'a', 'a', %s) returning id",
            (case, fact["id"], person, action, correction),
        ).fetchone()
        for person, action, correction in (
            (None, "remapped", None),
            (facts_world["expert"], "correct", uuid.uuid4()),
        )
    ]
    for (row_id,) in ids:  # type: ignore[misc]
        same = admin.execute(
            """select r.hash = encode(sha256(convert_to(concat_ws(chr(31),
                r.id::text, r.case_id::text, r.fact_id::text, coalesce(r.person_id::text, ''), r.seq::text, r.action,
                coalesce(r.old_value, ''), coalesce(r.new_value, ''), coalesce(r.old_fields::text, ''),
                coalesce(r.new_fields::text, ''), coalesce(r.old_word_ids::text, ''),
                coalesce(r.new_word_ids::text, ''), coalesce(r.old_page_id::text, ''),
                coalesce(r.new_page_id::text, ''), coalesce(r.reason, ''),
                ((extract(epoch from r.at) * 1000000)::bigint)::text, r.correction_id::text, r.prev_hash),
                'UTF8')), 'hex')
               from app.fact_verifications r where r.id = %s""",
            (row_id,),
        ).fetchone()
        assert same == (True,)


async def test_pv03b_a_date_the_expert_types_where_there_was_none_is_not_labelled_as_printed(
    admin: psycopg.Connection, facts_world: dict[str, Any]
) -> None:
    fact = await checked_fact(admin, facts_world, None, None)
    assert (await edit(facts_world, fact, event_date="2021-03-04")).status_code == 200
    assert date_row(admin, fact["id"]) == (date_of("2021-03-04"), "expert_entered")


async def test_pv03b_a_date_the_expert_changes_is_not_labelled_as_read_from_the_page(
    admin: psycopg.Connection, facts_world: dict[str, Any]
) -> None:
    fact = await checked_fact(admin, facts_world, "2020-01-02", "printed")
    assert (await edit(facts_world, fact, event_date="2021-03-04")).status_code == 200
    assert date_row(admin, fact["id"]) == (date_of("2021-03-04"), "expert_entered")


async def test_pv03b_control_an_edit_that_leaves_the_date_alone_keeps_its_basis(
    admin: psycopg.Connection, facts_world: dict[str, Any]
) -> None:
    fact = await checked_fact(admin, facts_world, "2020-01-02", "printed")
    assert (await edit(facts_world, fact, event_date="2020-01-02")).status_code == 200
    assert date_row(admin, fact["id"]) == (date_of("2020-01-02"), "printed")


def date_of(text: str) -> Any:
    import datetime as dt

    return dt.date.fromisoformat(text)


# PV-12 -----------------------------------------------------------------------------------------------------------

TEXT = "first typing of an unreadable page"
LATER = "second typing of the same page"


@pytest.fixture
def calls() -> Iterator[list[Any]]:
    seen: list[Any] = []

    async def extract(conn: psycopg.AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> uuid.UUID:
        seen.append(page_id)
        return uuid.uuid4()

    async def pages(conn: psycopg.AsyncConnection, case_id: uuid.UUID, page_ids: Any) -> uuid.UUID:
        seen.append(list(page_ids))
        return uuid.uuid4()

    with extract_page_text_entry.override(extract), process_pages_entry.override(pages):
        yield seen


@pytest.fixture
async def page_world(seed: Seed, admin: psycopg.Connection, client_for: Any) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    doc = seed.document(case)
    page = seed.page(case, doc, 7)
    admin.execute("update app.pages set unreadable = true where id = %s", (page,))
    return {"case": case, "expert": expert, "page": page, "client": await client_for(expert)}


def audit(admin: psycopg.Connection, w: dict[str, Any], action: str) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select person_id, target, at from app.audit_log where case_id = %s and action = %s order by seq",
        (w["case"], action),
    ).fetchall()


async def test_pv12_every_typed_transcription_is_kept_with_who_and_when_and_audited(
    admin: psycopg.Connection, aconn: psycopg.AsyncConnection, page_world: dict[str, Any], calls: list[Any]
) -> None:
    w = page_world
    url = f"/v1/cases/{w['case']}/pages/7/transcription"
    for text in (TEXT, LATER):
        assert (await w["client"].put(url, json={"text": text})).status_code == 202
    rows = admin.execute(
        "select person_id, text, at from app.page_transcriptions where page_id = %s order by at, id", (w["page"],)
    ).fetchall()
    assert len(rows) == 2 and all(r[0] == w["expert"] and r[2] is not None for r in rows)
    first = await CRYPTO.decrypt(aconn, w["case"], bytes(rows[0][1]), purpose=transcription_purpose(w["page"]))
    assert first.decode() == TEXT  # the earlier typing survives the overwrite
    saved = audit(admin, w, "page.transcription_saved")
    assert [(a[0], a[1]) for a in saved] == [(w["expert"], f"page:{w['page']}")] * 2
    assert not any(TEXT in str(a) or LATER in str(a) for a in saved)  # never page text in an audit row


async def test_pv12_a_change_of_relevance_or_clearer_copy_keeps_the_earlier_value_and_is_audited(
    admin: psycopg.Connection, page_world: dict[str, Any]
) -> None:
    w = page_world
    url = f"/v1/cases/{w['case']}/pages/7"
    for body in ({"relevance": "relevant"}, {"relevance": "nothing"}, {"needs_clearer_copy": True}):
        assert (await w["client"].patch(url, json=body)).status_code == 200
    rows = admin.execute(
        "select field, old_value, new_value, person_id from app.page_changes where page_id = %s order by at, id",
        (w["page"],),
    ).fetchall()
    assert [r[:3] for r in rows] == [
        ("relevance", None, "relevant"),
        ("relevance", "relevant", "nothing"),
        ("needs_clearer_copy", "false", "true"),
    ]
    assert all(r[3] == w["expert"] for r in rows)
    assert len(audit(admin, w, "page.changed")) == 3


async def test_pv12_relevance_sent_with_a_transcription_is_kept_too(
    admin: psycopg.Connection, page_world: dict[str, Any], calls: list[Any]
) -> None:
    w = page_world
    url = f"/v1/cases/{w['case']}/pages/7/transcription"
    assert (await w["client"].put(url, json={"text": TEXT, "relevance": "background"})).status_code == 202
    rows = admin.execute(
        "select field, old_value, new_value from app.page_changes where page_id = %s", (w["page"],)
    ).fetchall()
    assert rows == [("relevance", None, "background")]


async def test_pv12_turning_and_rotating_a_page_leave_an_audit_row(
    admin: psycopg.Connection, page_world: dict[str, Any], calls: list[Any]
) -> None:
    w = page_world
    base = f"/v1/cases/{w['case']}/pages"
    assert (await w["client"].put(f"{base}/7/turn", json={"degrees": 90})).status_code == 200
    assert (await w["client"].put(f"{base}/7/turn", json={"degrees": 180})).status_code == 200
    assert (await w["client"].post(f"{base}/{w['page']}/rotate", json={"degrees": 90})).status_code == 202
    assert [(a[0], a[1]) for a in audit(admin, w, "page.turned")] == [(w["expert"], f"page:{w['page']}")] * 2
    assert [(a[0], a[1]) for a in audit(admin, w, "page.rotated")] == [(w["expert"], f"page:{w['page']}")]


async def test_pv12_taking_a_flag_off_keeps_who_set_it_and_who_took_it_off(
    seed: Seed, admin: psycopg.Connection, client_for: Any
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    page = seed.page(case, seed.document(case), 1)
    fact_id = insert_fact(admin, case, page, "zz_result", {}, "val", ["w1"], status="verified")
    client = await client_for(expert)
    flag = f"/v1/cases/{case}/facts/{fact_id}/flag-point"
    assert (await client.post(flag)).status_code == 200
    assert (await client.delete(flag)).status_code == 200
    assert admin.execute("select count(*) from app.fact_flags where fact_id = %s", (fact_id,)).fetchone() == (0,)
    row = admin.execute(
        "select flagged_for, flagged_by, flagged_at is not null, removed_by, at is not null"
        " from app.fact_flag_history where fact_id = %s",
        (fact_id,),
    ).fetchone()
    assert row == ("points", expert, True, expert, True)
    w = {"case": case}
    assert [a[0] for a in audit(admin, w, "fact.flag_added")] == [expert]
    assert [a[0] for a in audit(admin, w, "fact.flag_removed")] == [expert]


# PV-12 (who may read the history): a case's own work stays with the case, a page's own history goes with the page.
# Two cases may read one bundle (ADR 0034, a replacement case). `fact_flag_history` is the case's own reading
# (flags carry the case, like `facts` and `fact_flags`), so it is read through the fact's case. `page_changes` and
# `page_transcriptions` keep the earlier values of columns of the shared page row (relevance, clearer copy needed,
# the typed text), so they go with the page, as `ocr_words` do: both cases see the page they share.


@pytest.fixture
def shared_world(seed: Seed, admin: psycopg.Connection) -> dict[str, Any]:
    a, b = seed.person(), seed.person()
    case_a = seed.case(seed.org(kind="team", members={a: "owner"}), a, status="active")
    case_b = seed.case(seed.org(kind="team", members={b: "owner"}), b, status="active")
    page = seed.page(case_a, seed.document(case_a), 1)
    admin.execute(
        "update app.cases set bundle_id = (select bundle_id from app.cases where id = %s) where id = %s",
        (case_a, case_b),
    )
    fact_a = insert_fact(admin, case_a, page, "zz_result", {}, "val", ["w1"], status="verified")
    admin.execute(
        "insert into app.fact_flag_history (fact_id, page_id, flagged_for, flagged_by, flagged_at, removed_by)"
        " values (%s, %s, 'points', %s, now(), %s)",
        (fact_a, page, a, a),
    )
    admin.execute(
        "insert into app.page_changes (page_id, person_id, field, old_value, new_value)"
        " values (%s, %s, 'relevance', null, 'background')",
        (page, a),
    )
    admin.execute("insert into app.page_transcriptions (page_id, person_id, text) values (%s, %s, %s)", (page, a, b"x"))
    return {"a": a, "b": b, "case_a": case_a, "case_b": case_b, "page": page, "fact_a": fact_a}


def _seen(conn: psycopg.Connection, person: uuid.UUID, table: str) -> int:
    conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
    row = conn.execute(f"select count(*) from app.{table}").fetchone()  # noqa: S608
    conn.rollback()
    assert row is not None
    return int(row[0])


def test_pv12_a_flag_history_is_read_only_through_the_case_the_flag_belongs_to(
    api_conn: psycopg.Connection, shared_world: dict[str, Any]
) -> None:
    w = shared_world
    assert _seen(api_conn, w["b"], "fact_flag_history") == 0, "the other case on the bundle sees no flag history"
    assert _seen(api_conn, w["a"], "fact_flag_history") == 1


@pytest.mark.parametrize("table", ["page_changes", "page_transcriptions"])
def test_pv12_a_pages_own_history_goes_with_the_page_so_both_cases_on_the_bundle_read_it(
    api_conn: psycopg.Connection, shared_world: dict[str, Any], table: str
) -> None:
    w = shared_world
    assert _seen(api_conn, w["a"], table) == 1
    assert _seen(api_conn, w["b"], table) == 1


# PV-01 (after a re-read): a page read again replaces its words, so a checked fact must be carried onto the new words or
# say that it cannot be ("Please check", which keeps it out of every report). A checked fact left citing words that
# are gone, with nothing saying so, is a quote with no source.


def checked_facts_citing_missing_words(admin: psycopg.Connection, page: uuid.UUID) -> list[uuid.UUID]:
    """Checked facts of any case on the page that cite a word the page no longer has and carry no recheck flag."""
    return [
        r[0]
        for r in admin.execute(
            "select f.id from app.facts f where f.page_id = %s and f.status in ('verified', 'edited')"
            " and exists (select 1 from unnest(f.word_ids) w where not exists"
            "   (select 1 from app.ocr_words o where o.page_id = f.page_id and o.word_id = w))"
            " and not exists (select 1 from app.fact_flags g where g.fact_id = f.id and g.flagged_for = 'recheck')",
            (page,),
        ).fetchall()
    ]


async def test_pv01_after_a_re_read_every_checked_fact_cites_words_that_exist_or_is_flagged_please_check(
    h: Harness,  # noqa: F811
) -> None:
    first, _ = page_ids(h)
    kept = add_fact(h, first, "chest clear", await words_for(h, first, "chest clear"), "verified")
    lost = add_fact(h, first, "Seen", await words_for(h, first, "Seen"), "edited")
    await type_out(h, first, "review chest clear")
    assert checked_facts_citing_missing_words(h.admin, first) == []
    assert fact(h, kept)["word_ids"] == ["e2", "e3"], "the words that still show the value are carried over"
    flagged = h.admin.execute(
        "select person_id from app.fact_flags where fact_id = %s and flagged_for = 'recheck'", (lost,)
    ).fetchall()
    assert flagged == [(None,)], "the fact whose words are gone says so, and no person is named"
    assert fact(h, lost)["status"] == "edited", "its status is not touched: the expert decides"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "OPEN, pipeline-owned (audit 16 PV-01 residue): the re-read runs as one case's job and the worker's"
        " row-level rule on facts (foundation 0008, case_id = app.current_job_case_id()) hides the other"
        " case's checked facts on a shared bundle page, so pipeline service._settle / repo.checked_facts /"
        " repo.settle_facts never see them. It needs a security-definer re-map over every case on the bundle"
        " (and a recheck flag per case); remove this marker then."
    ),
)
async def test_pv01_after_a_re_read_by_the_other_case_on_a_shared_bundle_its_checked_facts_are_covered_too(
    h: Harness,  # noqa: F811
    seed: Seed,
) -> None:
    """A replacement case reads the same bundle (ADR 0034) and has its own checked facts on a page the first case
    reads again. The re-read runs as the first case's job, so it must still leave the other case's checked facts
    carried over or flagged; the job's own row-level rules hide them, so this fails unless the re-map reaches them."""
    first, _ = page_ids(h)
    other = seed.person()
    case_b = seed.case(seed.org(members={other: "owner"}), other, packs=LAUNCH.case_row())
    h.admin.execute(
        "update app.cases set bundle_id = (select bundle_id from app.cases where id = %s) where id = %s",
        (h.case_id, case_b),
    )
    row = h.admin.execute(
        "insert into app.facts (case_id, page_id, kind, value, word_ids) values (%s, %s, 'note', 'chest clear', %s)"
        " returning id",
        (case_b, first, await words_for(h, first, "chest clear")),
    ).fetchone()
    assert row is not None
    h.admin.execute("update app.facts set status = 'verified' where id = %s", (row[0],))
    await type_out(h, first, "review chest clear")
    assert checked_facts_citing_missing_words(h.admin, first) == []


# PV-09 (reader part) ---------------------------------------------------------------------------------------------
# A page is set aside as a copy only once the expert has confirmed it. A copy the pipeline merely suggested is still
# read, counted, listed and shows its own suggestions.


def mark_copy(admin: psycopg.Connection, original: Any, copy: Any, status: str) -> None:
    admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (original, copy))
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, duplicate_status) select id, bundle_id, %s from app.pages"
        " where id = %s on conflict (page_id) do update set duplicate_status = excluded.duplicate_status",
        (status, copy),
    )


@pytest.fixture
def copy_world(seed: Seed, admin: psycopg.Connection) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    doc = seed.document(case)
    original, copy = seed.page(case, doc, 1), seed.page(case, doc, 2)
    admin.execute("update app.pages set unreadable = true where id = %s", (copy,))
    return {"case": case, "expert": expert, "original": original, "copy": copy}


@pytest.mark.parametrize(("status", "counted"), [("suggested", 2), ("kept", 2), ("confirmed", 1)])
async def test_pv09_only_a_confirmed_copy_is_set_aside_in_the_page_counts(
    admin: psycopg.Connection, aconn: psycopg.AsyncConnection, copy_world: dict[str, Any], status: str, counted: int
) -> None:
    from app.features.reader import repo as reader_repo
    from app.features.reader.transcript import _pages_to_read

    mark_copy(admin, copy_world["original"], copy_world["copy"], status)
    assert await reader_repo.page_counts(aconn, copy_world["case"]) == (counted, 2 - counted)
    assert len(await _pages_to_read(aconn, copy_world["case"])) == counted
    assert len(await reader_repo.page_pictures(aconn, copy_world["case"])) == counted


@pytest.mark.parametrize(("status", "waiting"), [("suggested", 1), ("confirmed", 0)])
async def test_pv09_an_unconfirmed_copy_that_cannot_be_read_still_waits_to_be_typed(
    admin: psycopg.Connection, aconn: psycopg.AsyncConnection, copy_world: dict[str, Any], status: str, waiting: int
) -> None:
    from app.features.reader import repo as reader_repo

    mark_copy(admin, copy_world["original"], copy_world["copy"], status)
    assert [n for _, n in await reader_repo.pages_to_type(aconn, [copy_world["case"]])] == ([1] if waiting else [])
    queue = await reader_repo.unreadable_pages(aconn, copy_world["case"])
    assert [q["is_copy"] for q in queue] == [not waiting]


@pytest.mark.parametrize(("status", "set_aside"), [("suggested", False), ("confirmed", True)])
async def test_pv09_viewing_a_page_counts_unless_it_is_a_confirmed_copy(
    admin: psycopg.Connection, aconn: psycopg.AsyncConnection, copy_world: dict[str, Any], status: str, set_aside: bool
) -> None:
    from app.features.reader import transcript

    mark_copy(admin, copy_world["original"], copy_world["copy"], status)
    try:
        await transcript.record_seen(aconn, copy_world["case"], copy_world["expert"], page_no=2, picture_opened=True)
        refused = False
    except transcript.UnknownEntry:
        refused = True
    except transcript.NotReady:
        refused = False
    assert refused is set_aside


@pytest.mark.parametrize(("status", "shows"), [("suggested", True), ("confirmed", False)])
async def test_pv09_a_suggested_copy_shows_its_own_suggestions_a_confirmed_one_shows_the_originals(
    client_for: Any,
    aconn: psycopg.AsyncConnection,
    admin: psycopg.Connection,
    seed: Seed,
    status: str,
    shows: bool,
) -> None:
    expert = seed.person()
    case = seed.case(
        seed.org(kind="personal", members={expert: "owner"}), expert, status="active", packs=LAUNCH.case_row()
    )
    loaded = await load_stub_facts(aconn, case, pages=[118, 1], crypto=CRYPTO)
    mark_copy(admin, loaded[118].page_id, loaded[1].page_id, status)
    client = await client_for(expert)
    base = f"/v1/cases/{case}"
    assert (await client.post(f"{base}/pages/1/read", json={"dwell_seconds": 20})).status_code == 200
    facts = (await client.get(f"{base}/pages/1/facts")).json()["facts"]
    assert bool(facts) is shows


# Transcript export (PV-04 family: the file handed over is the file that was built) --------------------------------


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def export_services(monkeypatch: pytest.MonkeyPatch, storage: CaseFileStorage) -> None:
    from app.features.reader import transcript_jobs

    monkeypatch.setattr(transcript_jobs.services, "storage", storage)
    monkeypatch.setattr(transcript_jobs.services, "crypto", CRYPTO)


async def built_export(
    w: dict[str, Any], worker_db: Database, admin: psycopg.Connection, fmt: str = "docx"
) -> tuple[uuid.UUID, httpx.AsyncClient]:
    """Presses download, runs the build as the worker would and marks the job done; returns the job."""
    from app.core.jobs import JobContext
    from app.features.reader import transcript_jobs

    client: httpx.AsyncClient = w["client"]
    resp = await client.post(f"/v1/cases/{w['case']}/transcript/download", json={"format": fmt})
    job = uuid.UUID(resp.json()["job_id"])
    kind = transcript_jobs.KIND_PDF if fmt == "pdf" else transcript_jobs.KIND
    build = transcript_jobs.export_transcript_pdf if fmt == "pdf" else transcript_jobs.export_transcript
    await build(JobContext(worker_db, job, w["case"], kind, 1))
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    return job, client


@pytest.fixture
async def export_world(
    seed: Seed, aconn: psycopg.AsyncConnection, client_for: Any, export_services: None
) -> dict[str, Any]:
    from tests.support.transcript_world import Spec, add_document, add_page

    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    doc = await add_document(aconn, case)
    first = await add_page(aconn, CRYPTO, case, doc, 1, [Spec("zz_session", ("note one",))])
    copy = await add_page(aconn, CRYPTO, case, doc, 2, [Spec("zz_session", ("note two",))], duplicate_of=first.page_id)
    held = await add_page(aconn, CRYPTO, case, doc, 3, [Spec("zz_session", ("note three",))], quarantined=True)
    await add_page(aconn, CRYPTO, case, doc, 4, [Spec("zz_session", ("note four",))])
    await aconn.execute(
        "insert into app.page_analysis (page_id, bundle_id, duplicate_status) select id, bundle_id, 'confirmed'"
        " from app.pages where id = %s on conflict (page_id) do update set duplicate_status = 'confirmed'",
        (copy.page_id,),
    )
    return {"case": case, "expert": expert, "client": await client_for(expert), "held": held.page_id}


@pytest.mark.parametrize("fmt", ["docx", "pdf"])
async def test_export_the_file_hash_is_recorded_when_built_and_sent_with_the_download(
    admin: psycopg.Connection, worker_db: Database, export_world: dict[str, Any], fmt: str
) -> None:
    import hashlib

    job, client = await built_export(export_world, worker_db, admin, fmt)
    row = admin.execute("select fmt, sha256 from app.transcript_files where job_id = %s", (job,)).fetchone()
    assert row is not None
    resp = await client.get(f"/v1/cases/{export_world['case']}/transcript/download/{job}")
    assert resp.status_code == 200, resp.text
    digest = hashlib.sha256(resp.content).hexdigest()
    assert row == (fmt, digest)
    assert resp.headers["x-content-sha256"] == digest


async def test_export_a_file_swapped_in_storage_after_it_was_built_is_refused(
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    worker_db: Database,
    export_world: dict[str, Any],
    storage: CaseFileStorage,
) -> None:
    from app.features.reader import transcript_jobs

    job, client = await built_export(export_world, worker_db, admin)
    path = transcript_jobs.transcript_path(export_world["case"], job)
    await storage.put(path, await CRYPTO.encrypt(aconn, export_world["case"], b"not the file built", purpose=path))
    resp = await client.get(f"/v1/cases/{export_world['case']}/transcript/download/{job}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "hash_mismatch"


async def _file_with_no_hash(
    export_world: dict[str, Any],
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    seed: Seed,
    storage: CaseFileStorage,
    created: str | None,
) -> uuid.UUID:
    """A finished transcript job whose file is stored but whose hash row was never written (how an old job, or a
    hand-made one, looks). `created` back-dates the job; None leaves it created now."""
    from app.features.reader import transcript_jobs

    job = seed.job(export_world["case"], kind=transcript_jobs.KIND, requested_by=export_world["expert"])
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    if created is not None:
        admin.execute("update app.jobs set created_at = %s::timestamptz where id = %s", (created, job))
    path = transcript_jobs.transcript_path(export_world["case"], job)
    await storage.put(path, await CRYPTO.encrypt(aconn, export_world["case"], b"a file", purpose=path))
    return job


async def test_export_a_file_with_no_recorded_hash_is_refused_for_a_job_made_after_hashes_were_recorded(
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    seed: Seed,
    export_world: dict[str, Any],
    storage: CaseFileStorage,
) -> None:
    job = await _file_with_no_hash(export_world, admin, aconn, seed, storage, created=None)
    resp = await export_world["client"].get(f"/v1/cases/{export_world['case']}/transcript/download/{job}")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "hash_mismatch"
    audited = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and target = %s",
        (export_world["case"], f"export:{job}"),
    ).fetchone()
    assert audited == (0,), "a refused file is not recorded as handed over"


async def test_export_control_a_file_of_a_job_made_before_hashes_were_recorded_is_still_handed_over(
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    seed: Seed,
    export_world: dict[str, Any],
    storage: CaseFileStorage,
) -> None:
    job = await _file_with_no_hash(export_world, admin, aconn, seed, storage, created="2026-06-01T00:00:00Z")
    resp = await export_world["client"].get(f"/v1/cases/{export_world['case']}/transcript/download/{job}")
    assert resp.status_code == 200
    assert resp.content == b"a file"


async def test_export_the_recorded_hash_cannot_be_changed_by_the_application_roles(
    admin: psycopg.Connection, worker_db: Database, export_world: dict[str, Any], worker_dsn: str
) -> None:
    job, _ = await built_export(export_world, worker_db, admin)
    assert admin.execute("select count(*) from app.transcript_files where job_id = %s", (job,)).fetchone() == (1,)
    with psycopg.connect(worker_dsn, autocommit=True) as conn:
        for statement in (
            "update app.transcript_files set sha256 = repeat('0', 64) where job_id = %s",
            "delete from app.transcript_files where job_id = %s",
        ):
            with pytest.raises(psycopg.Error) as refused:
                conn.execute(statement, (job,))
            assert refused.value.sqlstate in ("42501", REFUSED)  # no privilege, or the append-only rule


async def test_export_the_file_lists_which_pages_are_absent_and_why(
    admin: psycopg.Connection, worker_db: Database, export_world: dict[str, Any]
) -> None:
    import io

    import docx

    from app.features.reader.transcript_words import word

    job, client = await built_export(export_world, worker_db, admin)
    resp = await client.get(f"/v1/cases/{export_world['case']}/transcript/download/{job}")
    text = [p.text for p in docx.Document(io.BytesIO(resp.content)).paragraphs]
    copies = next(t for t in text if t.startswith(word("en", "reason_copy")))
    held = next(t for t in text if t.startswith(word("en", "reason_held")))
    assert copies.endswith("2") and held.endswith("3")
    assert not any("note" in t and "two" in t for t in text), "the set-aside page's words are not in the file"
