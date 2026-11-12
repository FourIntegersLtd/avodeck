"""Audit 16: document provenance and output integrity (PV-06, PV-13, PV-18).

Each test asserts the SAFE outcome. PV-06 and PV-13 run SQL as the restricted `app_api` / `app_worker` roles, or as the
table owner (`app_migrator`, which every `security definer` function runs as), against a small world of their own.
Statements that a fix must refuse run in a savepoint; whatever a statement did is checked from the admin connection.
Nothing here resembles a record (rule 6).
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.ai_audit import PACK, VALID, task
from tests.support.seed import Seed

if not os.environ.get("TEST_DATABASE_ADMIN_URL"):  # never fall back to a repo-local port: it may be a founder database
    pytest.skip("TEST_DATABASE_ADMIN_URL is not set", allow_module_level=True)

pytestmark = pytest.mark.anyio

AV409 = "AV409"
NOT_ALLOWED = {AV409, "42501"}  # a guard refusing, or a missing privilege


@dataclass(frozen=True)
class World:
    expert: uuid.UUID
    case: uuid.UUID
    document: uuid.UUID
    other_document: uuid.UUID
    page: uuid.UUID


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> World:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    document, other = seed.document(case), seed.document(case)
    page = seed.page(case, document, 1)
    admin.execute("update app.documents set storage_path = 'zz/doc', pages = 1 where id = %s", (document,))
    return World(expert, case, document, other, page)


@pytest.fixture
def api(api_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(api_dsn, connect_timeout=20) as conn:
        yield conn
        conn.rollback()


@pytest.fixture
def worker(worker_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(worker_dsn, connect_timeout=20) as conn:
        yield conn
        conn.rollback()


def _as(conn: psycopg.Connection, person: uuid.UUID | None = None, job_case: uuid.UUID | None = None) -> None:
    if person is not None:
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
    if job_case is not None:
        conn.execute("select set_config('app.job_case_id', %s, true)", (str(job_case),))


def _try(conn: psycopg.Connection, query: str, params: tuple[object, ...] = ()) -> tuple[str | None, int]:
    """(SQLSTATE if refused, rows changed) for one statement, in its own savepoint."""
    try:
        with conn.transaction():
            return None, conn.execute(query, params).rowcount  # type: ignore[arg-type]
    except psycopg.Error as exc:
        return exc.sqlstate, 0


def _value(admin: psycopg.Connection, table: str, column: str, row: uuid.UUID) -> object:
    got = admin.execute(f"select {column} from app.{table} where id = %s", (row,)).fetchone()  # noqa: S608
    assert got is not None
    return got[0]


# --- PV-06: source-derived columns are protected at the database ------------------------------------------------------


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("sha256", "1" * 64),
        ("storage_path", "zz/other"),
        ("filename", "other.pdf"),
        ("kind", "other_kind"),
        ("pages", 99),
    ],
)
def test_pv06_the_api_cannot_rewrite_what_a_document_is(
    column: str, value: object, api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    before = _value(admin, "documents", column, world.document)
    _as(api, world.expert)
    state, _ = _try(api, f"update app.documents set {column} = %s where id = %s", (value, world.document))  # noqa: S608
    assert state in NOT_ALLOWED
    assert _value(admin, "documents", column, world.document) == before


@pytest.mark.parametrize(("column", "value"), [("sha256", "2" * 64), ("storage_path", "zz/other"), ("kind", "x")])
def test_pv06_not_even_the_table_owner_can_rewrite_what_a_document_is(
    column: str, value: object, admin: psycopg.Connection, world: World
) -> None:
    """`app_migrator` owns the tables and every `security definer` function runs as it."""
    before = _value(admin, "documents", column, world.document)
    with admin.transaction():
        admin.execute("set local role app_migrator")
        state, _ = _try(admin, f"update app.documents set {column} = %s where id = %s", (value, world.document))  # noqa: S608
        assert state in NOT_ALLOWED
        raise psycopg.Rollback
    assert _value(admin, "documents", column, world.document) == before


def test_pv06_a_documents_file_can_still_be_cleared_when_it_is_purged(admin: psycopg.Connection, world: World) -> None:
    """Control: the purge flows set `storage_path` to null."""
    with admin.transaction():
        admin.execute("set local role app_migrator")
        state, changed = _try(admin, "update app.documents set storage_path = null where id = %s", (world.document,))
        assert (state, changed) == (None, 1)
        raise psycopg.Rollback


def test_pv06_control_the_api_can_still_change_a_documents_provenance_notes(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    _as(api, world.expert)
    state, changed = _try(api, "update app.documents set obtained_how = 'by post' where id = %s", (world.document,))
    assert (state, changed) == (None, 1)


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("document_id", None),  # filled in the test: the other document
        ("image_path", "zz/forged.png"),
        ("text_source", "ocr"),
        ("ocr_confidence", 0.99),
        ("handwriting", True),
    ],
)
def test_pv06_the_api_cannot_rewrite_what_a_page_was_read_from(
    column: str, value: object, api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    if column == "document_id":
        value = world.other_document
    before = _value(admin, "pages", column, world.page)
    _as(api, world.expert)
    state, _ = _try(api, f"update app.pages set {column} = %s where id = %s", (value, world.page))  # noqa: S608
    assert state in NOT_ALLOWED
    assert _value(admin, "pages", column, world.page) == before


def test_pv06_a_page_never_moves_to_another_document_for_any_role(admin: psycopg.Connection, world: World) -> None:
    with admin.transaction():
        admin.execute("set local role app_migrator")
        state, _ = _try(
            admin, "update app.pages set document_id = %s where id = %s", (world.other_document, world.page)
        )
        assert state in NOT_ALLOWED
        raise psycopg.Rollback
    assert _value(admin, "pages", "document_id", world.page) == world.document


def test_pv06_the_worker_cannot_move_a_page_to_another_document(
    worker: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    _as(worker, job_case=world.case)
    state, _ = _try(worker, "update app.pages set document_id = %s where id = %s", (world.other_document, world.page))
    assert state in NOT_ALLOWED
    assert _value(admin, "pages", "document_id", world.page) == world.document


def test_pv06_control_the_worker_still_records_what_it_read(
    worker: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    _as(worker, job_case=world.case)
    state, changed = _try(
        worker,
        "update app.pages set image_path = 'zz/p1.png', text_source = 'ocr', ocr_confidence = 0.9 where id = %s",
        (world.page,),
    )
    assert (state, changed) == (None, 1)


def test_pv06_control_the_api_can_still_turn_flag_and_reset_a_page(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    admin.execute("update app.pages set image_path = 'zz/p1.png', text_source = 'ocr' where id = %s", (world.page,))
    _as(api, world.expert)
    assert _try(api, "update app.pages set rotation = 90, relevance = 'zz' where id = %s", (world.page,)) == (None, 1)
    # read-again: the picture and the words are cleared so the worker reads the page afresh
    assert _try(api, "update app.pages set image_path = null, text_source = null where id = %s", (world.page,)) == (
        None,
        1,
    )


# --- PV-13: the audit log ---------------------------------------------------------------------------------------------


def test_pv13a_a_writer_cannot_back_date_an_audit_row(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    _as(api, world.expert)
    target = f"zz:backdate:{uuid.uuid4()}"
    state, _ = _try(
        api,
        "insert into app.audit_log (person_id, case_id, action, target, at)"
        " values (app.current_person_id(), %s, 'zz.backdated', %s, now() - interval '400 days')",
        (world.case, target),
    )
    api.commit()
    got = admin.execute("select at from app.audit_log where target = %s", (target,)).fetchone()
    assert state is not None or (got is not None and got[0] > datetime.now(UTC) - timedelta(minutes=5))


def test_pv13a_control_an_ordinary_audit_row_is_stamped_at_insert(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    _as(api, world.expert)
    target = f"zz:stamp:{uuid.uuid4()}"
    assert _try(
        api,
        "insert into app.audit_log (person_id, case_id, action, target)"
        " values (app.current_person_id(), %s, 'zz.stamped', %s)",
        (world.case, target),
    ) == (None, 1)
    api.commit()
    got = admin.execute("select at from app.audit_log where target = %s", (target,)).fetchone()
    assert got is not None and got[0] > datetime.now(UTC) - timedelta(minutes=5)


@pytest.mark.parametrize(
    "statement",
    ["update app.audit_log set target = 'zz:changed' where seq = %s", "delete from app.audit_log where seq = %s"],
)
def test_pv13b_the_table_owner_path_cannot_change_or_remove_an_audit_row(
    statement: str, admin: psycopg.Connection, world: World
) -> None:
    """A `security definer` function runs as `app_migrator`; the revoked grants never stopped it."""
    seq = admin.execute(
        "insert into app.audit_log (person_id, case_id, action, target) values (%s, %s, 'zz.keep', 'zz:keep')"
        " returning seq",
        (world.expert, world.case),
    ).fetchone()
    assert seq is not None
    with admin.transaction():
        admin.execute("set local role app_migrator")
        state, changed = _try(admin, statement, (seq[0],))
        assert state in NOT_ALLOWED, f"the owner path changed {changed} audit row(s)"
        raise psycopg.Rollback
    assert admin.execute("select target from app.audit_log where seq = %s", (seq[0],)).fetchone() == ("zz:keep",)


def test_pv13b_control_the_purge_flow_still_writes_its_own_audit_row(admin: psycopg.Connection, world: World) -> None:
    with admin.transaction():
        admin.execute("set local role app_migrator")
        state, changed = _try(
            admin,
            "insert into app.audit_log (case_id, action, target) values (%s, 'zz.owner_insert', 'zz:x')",
            (world.case,),
        )
        assert (state, changed) == (None, 1)
        raise psycopg.Rollback


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=3)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def test_pv13c_the_run_time_check_names_a_changed_audit_row(
    worker_db: Database, admin: psycopg.Connection, world: World
) -> None:
    from app.core import chain_check

    admin.execute("delete from app.chain_checks")
    seq = admin.execute(
        "insert into app.audit_log (person_id, case_id, action, target) values (%s, %s, 'zz.tamper', 'zz:original')"
        " returning seq",
        (world.expert, world.case),
    ).fetchone()
    assert seq is not None
    admin.execute("update app.audit_log set target = 'zz:changed' where seq = %s", (seq[0],))
    try:
        report = await chain_check.verify_chains(worker_db, audit_rows=10_000_000, cases=0)
        assert seq[0] in report.audit_breaks
    finally:
        admin.execute("update app.audit_log set target = 'zz:original' where seq = %s", (seq[0],))


async def test_pv13c_the_run_time_check_works_through_the_log_within_its_budget(
    worker_db: Database, admin: psycopg.Connection, world: World
) -> None:
    from app.core import chain_check

    admin.execute("delete from app.chain_checks")
    for i in range(5):
        admin.execute(
            "insert into app.audit_log (person_id, case_id, action, target) values (%s, %s, 'zz.step', %s)",
            (world.expert, world.case, f"zz:{i}"),
        )
    first = await chain_check.verify_chains(worker_db, audit_rows=2, cases=0)
    second = await chain_check.verify_chains(worker_db, audit_rows=2, cases=0)
    assert first.audit_rows_checked <= 2 and second.audit_rows_checked <= 2
    assert first.audit_rows_checked == 2 and second.audit_rows_checked == 2, "each round moves on, not the same rows"


async def test_pv13c_the_run_time_check_names_a_changed_page_read(
    worker_db: Database, admin: psycopg.Connection, world: World
) -> None:
    from app.core import chain_check

    admin.execute("delete from app.chain_checks")
    admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 5)",
        (world.case, world.page, world.expert),
    )
    admin.execute("set session_replication_role = replica")  # the append-only trigger would refuse a plain update
    admin.execute("update app.page_reads set dwell_seconds = 6000 where case_id = %s", (world.case,))
    admin.execute("set session_replication_role = origin")
    report = await chain_check.verify_chains(worker_db, audit_rows=0, cases=100_000)
    assert ("page_reads", world.case) in report.case_breaks


async def test_pv13c_control_an_untouched_case_chain_reports_no_break(
    worker_db: Database, admin: psycopg.Connection, world: World
) -> None:
    from app.core import chain_check

    admin.execute("delete from app.chain_checks")
    admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 5)",
        (world.case, world.page, world.expert),
    )
    report = await chain_check.verify_chains(worker_db, audit_rows=0, cases=100_000)
    assert world.case not in {c for _, c in report.case_breaks}
    assert report.cases_checked >= 1


async def test_pv13c_a_break_raises_a_patient_free_alert_from_a_registered_sweep(
    worker_db: Database, admin: psycopg.Connection, world: World, caplog: pytest.LogCaptureFixture
) -> None:
    import app.worker.main  # noqa: F401 - what the worker imports is what runs in production
    from app.core import chain_check
    from app.core.jobs import registry

    assert chain_check.SWEEP in registry.database_sweeps, "the worker runs the check every housekeeping round"
    admin.execute("delete from app.chain_checks")
    seq = admin.execute(
        "insert into app.audit_log (person_id, case_id, action, target) values (%s, %s, 'zz.tamper', 'zz:PATIENTWORD')"
        " returning seq",
        (world.expert, world.case),
    ).fetchone()
    assert seq is not None
    admin.execute("update app.audit_log set target = 'zz:PATIENTWORD2' where seq = %s", (seq[0],))
    # one round checks a budgeted slice from the cursor, and a shared test database holds more rows than a round
    # reads: start the cursor just before the row this test broke
    admin.execute(
        "insert into app.chain_checks (name, last_seq, last_hash) select 'audit_log', seq, hash from app.audit_log"
        " where seq < %s order by seq desc limit 1 on conflict (name) do update set last_seq = excluded.last_seq,"
        " last_hash = excluded.last_hash",
        (seq[0],),
    )
    try:
        with caplog.at_level(logging.ERROR):
            await registry.database_sweeps[chain_check.SWEEP](worker_db)
    finally:
        admin.execute("update app.audit_log set target = 'zz:PATIENTWORD' where seq = %s", (seq[0],))
    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "a break is an error-level event, so it reaches the ops alert (Sentry)"
    assert "PATIENTWORD" not in caplog.text and str(world.case) not in caplog.text


# --- PV-18: the processing version reaches outputs --------------------------------------------------------------------


async def test_pv18_an_ai_call_names_its_job_so_the_code_version_is_recoverable(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, world: World
) -> None:
    from app.core.ai.kit import AiKit

    job = seed.job(world.case)
    admin.execute("update app.jobs set code_version = 'zz-build-42' where id = %s", (job,))

    async def no_sleep(_seconds: float) -> None:
        return None

    kit = AiKit(cell(), ScriptedBedrock([response(VALID)]), sleep=no_sleep)
    async with worker_db.job(world.case) as conn:
        await kit.run(conn, task(), pack=PACK, case_id=world.case, text="zz text", job_id=job)
    got = admin.execute(
        "select j.code_version from app.ai_calls c join app.jobs j on j.id = c.job_id where c.case_id = %s",
        (world.case,),
    ).fetchall()
    assert got == [("zz-build-42",)]


async def test_pv18_an_ocr_call_is_recorded_with_the_model_and_the_api_version(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, world: World
) -> None:
    from app.core.ai.ocr import OcrResult, record_call

    job = seed.job(world.case)
    result = OcrResult(model="prebuilt-layout", pages=(), api_version="2024-11-30")
    async with worker_db.job(world.case) as conn:
        await record_call(conn, case_id=world.case, task="layout", result=result, region="zz-region", job_id=job)
    got = admin.execute(
        "select task_id, model_id, api_version, region, job_id, outcome from app.ai_calls where case_id = %s",
        (world.case,),
    ).fetchall()
    assert got == [("ocr.layout", "prebuilt-layout", "2024-11-30", "zz-region", job, "ok")]


def test_pv18_the_azure_client_reports_the_api_version_it_called() -> None:
    from app.core.ai import azure_ocr

    assert azure_ocr._result("prebuilt-layout", {}).api_version == azure_ocr.API_VERSION


async def test_pv18_control_a_job_keeps_the_code_version_it_was_queued_under(
    api_db: Database, admin: psycopg.Connection, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.jobs import registry
    from tests.support.db import scope_for

    monkeypatch.setenv("CODE_VERSION", "zz-build-7")
    async with api_db.request(scope_for(world.expert)) as conn:
        job = await registry.enqueue(conn, world.case, "process_bundle")
    assert admin.execute("select code_version from app.jobs where id = %s", (job,)).fetchone() == ("zz-build-7",)


# --- audit 16 follow-ups: the expert-entered date basis and the one definition of a set-aside page -------------------


def test_pv_followup_the_expert_entered_date_basis_is_a_shared_constant_the_database_accepts(
    admin: psycopg.Connection, world: World
) -> None:
    from app.core.contracts.fact_dates import DATE_BASIS_EXPERT, DATE_BASIS_PRINTED

    assert DATE_BASIS_EXPERT == "expert_entered"
    assert DATE_BASIS_EXPERT != DATE_BASIS_PRINTED
    fact = admin.execute(
        "insert into app.facts (case_id, page_id, kind, word_ids, event_date, date_basis)"
        " values (%s, %s, 'zz_kind', array['w1'], '2020-01-02', %s) returning date_basis",
        (world.case, world.page, DATE_BASIS_EXPERT),
    ).fetchone()
    assert fact == (DATE_BASIS_EXPERT,)


SET_ASIDE_STATES = {  # state -> is the page set aside (pipeline's NOT_SET_ASIDE is the reference)
    "plain": False,
    "suggested_copy": False,  # a copy the pipeline only suggested is still read and counted
    "kept_copy": False,
    "quarantined": True,
    "replaced": True,
    "confirmed_copy": True,
}


def _page_in_state(admin: psycopg.Connection, seed: Seed, world: World, state: str, number: int) -> uuid.UUID:
    page = seed.page(world.case, world.document, number)
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id)"
        " values (%s, (select bundle_id from app.cases where id = %s))",
        (page, world.case),
    )
    original = world.page
    if state == "quarantined":
        admin.execute("update app.pages set quarantined = true where id = %s", (page,))
    elif state == "replaced":
        admin.execute("update app.pages set superseded_by_page_id = %s where id = %s", (original, page))
    elif state in ("suggested_copy", "kept_copy", "confirmed_copy"):
        status = state.split("_")[0]
        admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (original, page))
        admin.execute("update app.page_analysis set duplicate_status = %s where page_id = %s", (status, page))
    return page


def test_pv_followup_one_shared_definition_says_which_pages_are_set_aside() -> None:
    from app.core.contracts.set_aside import is_set_aside

    for state, expected in SET_ASIDE_STATES.items():
        quarantined = state == "quarantined"
        replaced = "zz" if state == "replaced" else None
        copy_of = "zz" if state.endswith("_copy") else None
        status = state.split("_")[0] if state.endswith("_copy") else None
        assert is_set_aside(quarantined, replaced, copy_of, status) is expected, state
    assert is_set_aside(False, None, "zz", None) is False  # a copy with no analysis row is not confirmed


@pytest.mark.parametrize("state", sorted(SET_ASIDE_STATES))
async def test_pv_followup_a_fact_on_a_set_aside_page_is_not_citable(
    api_db: Database, admin: psycopg.Connection, seed: Seed, world: World, state: str
) -> None:
    from app.core.contracts.facts import citable_facts
    from app.features.reader.log import ReaderPageReadLog
    from tests.support.db import scope_for

    page = _page_in_state(admin, seed, world, state, 2)
    fact = admin.execute(
        "insert into app.facts (case_id, page_id, kind, word_ids) values (%s, %s, 'zz_kind', array['w1']) returning id",
        (world.case, page),
    ).fetchone()
    assert fact is not None
    admin.execute("update app.facts set status = 'verified' where id = %s", (fact[0],))
    admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds, via) values (%s, %s, %s, 30, 'page')",
        (world.case, page, world.expert),
    )
    async with api_db.request(scope_for(world.expert)) as conn:
        got = {f.id for f in await citable_facts(conn, world.case, ReaderPageReadLog())}
    assert (fact[0] not in got) is SET_ASIDE_STATES[state], state


@pytest.mark.parametrize("state", sorted(SET_ASIDE_STATES))
async def test_pv_followup_the_entries_contract_counts_only_pages_that_are_not_set_aside(
    api_db: Database, admin: psycopg.Connection, seed: Seed, world: World, state: str
) -> None:
    from app.core.contracts.record_entries import entries_progress, entries_ready
    from tests.support.db import scope_for

    page = _page_in_state(admin, seed, world, state, 2)
    # the case's first page is plain and unread by the entries job; park it so only `page` is in question
    admin.execute("update app.pages set quarantined = true where id = %s", (world.page,))
    async with api_db.request(scope_for(world.expert)) as conn:
        progress = await entries_progress(conn, world.case)
        ready = await entries_ready(conn, world.case)
    aside = SET_ASIDE_STATES[state]
    assert progress.required == (0 if aside else 1), state
    assert ready is aside, (
        state
    )  # a page that is not set aside and not yet extracted keeps the entries from being ready
    assert page


# --- PV-20: the purge exemption cannot be claimed by a setting --------------------------------------------------
# The append-only and locked-row triggers once stood aside when `app.purging_case_id` named the row's case. Any role can
# set a custom setting, so a `security definer` function reached from an app_api or app_worker session (it runs as the
# table owner) could remove rows the guards protect. The exemption is now a marker only the purge function can write.

PURGE_ACTORS = ["app_api", "app_worker"]


def _claim_purge(conn: psycopg.Connection, case: uuid.UUID) -> None:
    """What an attacker can do from a session of either role: name the case as being purged."""
    conn.execute("select set_config('app.purging_case_id', %s, true)", (str(case),))


@pytest.fixture
def definer_delete(admin: psycopg.Connection, api: psycopg.Connection, worker: psycopg.Connection) -> Iterator[object]:
    """A `security definer` function owned by the table owner and callable by both roles (the shape of any app function
    that deletes), made for this test and dropped after it. Returns `run(role, case, person, statement, id)`."""
    name = f"app.zz_pv20_delete_{uuid.uuid4().hex[:8]}"
    admin.execute("set role app_migrator")
    admin.execute(
        f"create function {name}(p_sql text, p_id uuid) returns integer language plpgsql security definer"  # noqa: S608
        " set search_path = '' as $f$ declare n integer; begin execute p_sql using p_id;"
        " get diagnostics n = row_count; return n; end $f$"
    )
    admin.execute(f"grant execute on function {name}(text, uuid) to app_api, app_worker")
    admin.execute("reset role")
    admin.commit()

    def run(role: str, case: uuid.UUID, person: uuid.UUID, statement: str, row: uuid.UUID) -> str | None:
        conn = api if role == "app_api" else worker
        _as(conn, person=person if role == "app_api" else None, job_case=case if role == "app_worker" else None)
        _claim_purge(conn, case)  # what any session of the role can do
        state, _ = _try(conn, f"select {name}(%s, %s)", (statement, row))  # noqa: S608
        conn.rollback()
        return state

    try:
        yield run
    finally:
        admin.execute(f"drop function if exists {name}(text, uuid)")
        admin.commit()


@pytest.mark.parametrize("role", PURGE_ACTORS)
def test_pv20_a_page_read_cannot_be_removed_by_naming_the_case_as_purged(
    role: str, admin: psycopg.Connection, world: World, definer_delete: Any
) -> None:
    row = admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds, via) values (%s, %s, %s, 30, 'page')"
        " returning id",
        (world.case, world.page, world.expert),
    ).fetchone()
    assert row is not None
    state = definer_delete(role, world.case, world.expert, "delete from app.page_reads where id = $1", row[0])
    assert state == AV409, state
    assert admin.execute("select count(*) from app.page_reads where id = %s", (row[0],)).fetchone() == (1,)


def _locked_citation(admin: psycopg.Connection, seed: Seed, world: World) -> uuid.UUID:
    from tests.support.contracts import add_fact, add_report_version

    report = add_report_version(admin, world.case)
    paragraph = admin.execute(
        "insert into app.report_paragraphs (case_id, report_version_id, para_no, section, content_control_id, text)"
        " values (%s, %s, 1, 'zz_section', 'cc1', 'x'::bytea) returning id",
        (world.case, report),
    ).fetchone()
    fact = add_fact(admin, world.case, world.page)
    citation = admin.execute(
        "insert into app.citations (case_id, report_version_id, paragraph_id, cite_no, fact_id, fact_version,"
        " value_snapshot, quote_verbatim, page_id) values (%s, %s, %s, 1, %s, 1, 'x'::bytea, 'x'::bytea, %s)"
        " returning id",
        (world.case, report, paragraph[0] if paragraph else None, fact, world.page),
    ).fetchone()
    assert citation is not None
    admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("a" * 64, report))
    return citation[0]


@pytest.mark.parametrize("role", PURGE_ACTORS)
def test_pv20_a_citation_of_a_locked_report_cannot_be_removed_by_naming_the_case_as_purged(
    role: str, admin: psycopg.Connection, seed: Seed, world: World, definer_delete: Any
) -> None:
    citation = _locked_citation(admin, seed, world)
    state = definer_delete(role, world.case, world.expert, "delete from app.citations where id = $1", citation)
    assert state is not None, "the delete went through"
    assert admin.execute("select count(*) from app.citations where id = %s", (citation,)).fetchone() == (1,)


@pytest.mark.parametrize("role", PURGE_ACTORS)
def test_pv20_a_locked_report_version_cannot_be_removed_by_naming_the_case_as_purged(
    role: str, admin: psycopg.Connection, world: World, definer_delete: Any
) -> None:
    from tests.support.contracts import add_report_version

    report = add_report_version(admin, world.case, status="locked")
    state = definer_delete(role, world.case, world.expert, "delete from app.report_versions where id = $1", report)
    assert state is not None, "the delete went through"
    assert admin.execute("select count(*) from app.report_versions where id = %s", (report,)).fetchone() == (1,)


def _served_case_with_a_party(admin: psycopg.Connection, seed: Seed, world: World) -> uuid.UUID:
    from tests.support.contracts import add_report_version

    party = admin.execute(
        "insert into app.case_parties (case_id, role_key, name) values (%s, 'zz_role', 'x'::bytea) returning id",
        (world.case,),
    ).fetchone()
    assert party is not None
    add_report_version(admin, world.case, status="served")
    return party[0]


@pytest.mark.parametrize("role", PURGE_ACTORS)
def test_pv20_a_party_of_a_served_case_cannot_be_removed_by_naming_the_case_as_purged(
    role: str, admin: psycopg.Connection, seed: Seed, world: World, definer_delete: Any
) -> None:
    party = _served_case_with_a_party(admin, seed, world)
    state = definer_delete(role, world.case, world.expert, "delete from app.case_parties where id = $1", party)
    assert state == AV409, state
    assert admin.execute("select count(*) from app.case_parties where id = %s", (party,)).fetchone() == (1,)


def test_pv20_taking_a_flag_off_keeps_its_history_even_when_the_case_is_named_as_purged(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    from tests.support.contracts import add_fact

    fact = add_fact(admin, world.case, world.page)
    admin.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, 'zz_flag', %s)",
        (world.case, fact, world.expert),
    )
    _as(api, person=world.expert)
    _claim_purge(api, world.case)
    state, removed = _try(api, "delete from app.fact_flags where fact_id = %s", (fact,))
    assert (state, removed) == (None, 1)  # the expert may take a flag off; what is kept is the history row
    api.commit()
    got = admin.execute("select count(*) from app.fact_flag_history where fact_id = %s", (fact,)).fetchone()
    assert got == (1,), "the flag went off with no history row"


def test_pv20_control_the_purge_still_removes_a_locked_case_whole(admin: psycopg.Connection, world: World) -> None:
    from tests.support.contracts import add_report_version

    add_report_version(admin, world.case, status="served")
    admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds, via) values (%s, %s, %s, 30, 'page')",
        (world.case, world.page, world.expert),
    )
    admin.execute("select app.purge_case_rows(%s)", (world.case,))
    for table in ("report_versions", "page_reads"):
        left = admin.execute(f"select count(*) from app.{table} where case_id = %s", (world.case,)).fetchone()  # noqa: S608
        assert left == (0,), table
