"""The page-batch tables (migration 0136, ADR 0038): who sees them, the purge order and the purge lock.

The worker sees a case's runs and batches only inside a job on that case; the API role has no access.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.contracts import case_tables
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.packs import REPO
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

MIGRATIONS = REPO / "supabase" / "migrations"
INSERT_RUN = 'insert into app.pipeline_runs (job_id, case_id, state, batch_pages, "window") values (%s, %s, %s, 100, 2)'


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def _job(admin: psycopg.Connection, case_id: uuid.UUID) -> uuid.UUID:
    row = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'process_bundle') returning id",
        (case_id,),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def _run(admin: psycopg.Connection, case_id: uuid.UUID, state: str = "ocr_waiting") -> uuid.UUID:
    job = _job(admin, case_id)
    admin.execute(INSERT_RUN, (job, case_id, state))
    return job


def _batch(
    admin: psycopg.Connection, run: uuid.UUID, case_id: uuid.UUID, seq: int = 0, phase: str = "ocr"
) -> uuid.UUID:
    row = admin.execute(
        "insert into app.pipeline_batches (run_job_id, case_id, phase, seq, first_page_no, last_page_no, page_count)"
        " values (%s, %s, %s, %s, 1, 100, 100) returning id",
        (run, case_id, phase, seq),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def _case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(members={expert: "owner"}), expert)


def test_the_two_tables_are_no_longer_awaiting_a_migration() -> None:
    assert case_tables.AWAITING_SLICE_MIGRATION == {}
    assert "pipeline_runs" in case_tables.CASE_TABLES and "pipeline_batches" in case_tables.CASE_TABLES


def test_purge_order_lists_batches_then_runs_before_everything_else(admin: psycopg.Connection) -> None:
    row = admin.execute("select app.case_purge_order()").fetchone()
    assert row is not None
    assert row[0][:2] == ["pipeline_batches", "pipeline_runs"]
    assert list(case_tables.PURGE_ORDER) == row[0]


def test_the_migration_has_a_down_file_and_the_kind_has_an_action(admin: psycopg.Connection) -> None:
    assert (MIGRATIONS / "down" / "0136_pipeline_page_batches.sql").is_file()
    row = admin.execute("select action from app.job_kind_actions where kind = 'read_page_batch'").fetchone()
    assert row == ("case.upload",)


def test_states_phases_and_batch_uniqueness_are_checked(admin: psycopg.Connection, seed: Seed) -> None:
    case = _case(seed)
    with pytest.raises(psycopg.errors.CheckViolation):
        _run(admin, case, state="waiting")
    run = _run(admin, case)
    _batch(admin, run, case, seq=0)
    with pytest.raises(psycopg.errors.UniqueViolation):
        _batch(admin, run, case, seq=0)
    with pytest.raises(psycopg.errors.CheckViolation):
        _batch(admin, run, case, seq=1, phase="read")


async def test_the_worker_sees_only_its_own_cases_rows_and_the_api_none(
    admin: psycopg.Connection, seed: Seed, api_db: Database, worker_db: Database
) -> None:
    expert, stranger = seed.person(), seed.person()
    mine = seed.case(seed.org(members={expert: "owner"}), expert)
    theirs = seed.case(seed.org(members={stranger: "owner"}), stranger)
    my_run, their_run = _run(admin, mine), _run(admin, theirs)
    _batch(admin, my_run, mine)
    _batch(admin, their_run, theirs)
    async with worker_db.job(mine) as conn:
        cur = await conn.execute("select case_id from app.pipeline_runs")
        assert await cur.fetchall() == [(mine,)]
        cur = await conn.execute("select case_id from app.pipeline_batches")
        assert await cur.fetchall() == [(mine,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute(INSERT_RUN, (_job(admin, theirs), theirs, "ocr_waiting"))
    for table in ("pipeline_runs", "pipeline_batches"):
        async with api_db.request(scope_for(expert)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(f"select count(*) from app.{table}")  # noqa: S608


def test_a_batch_delete_outside_a_purge_does_not_wait_for_the_run_row(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed
) -> None:
    case = _case(seed)
    run = _run(admin, case)
    batch = _batch(admin, run, case)
    holder = psycopg.connect(admin_dsn, autocommit=False)
    try:
        holder.execute("select 1 from app.pipeline_runs where job_id = %s for update", (run,))
        with psycopg.connect(admin_dsn, autocommit=True) as other:
            other.execute("set lock_timeout = '1s'")
            other.execute("delete from app.pipeline_batches where id = %s", (batch,))
    finally:
        holder.rollback()
        holder.close()


def test_a_batch_delete_in_a_purge_waits_for_the_run_row_lock_first(
    admin: psycopg.Connection, admin_dsn: str, seed: Seed
) -> None:
    case = _case(seed)
    run = _run(admin, case)
    batch = _batch(admin, run, case)
    holder = psycopg.connect(admin_dsn, autocommit=False)
    holder.execute("select 1 from app.pipeline_runs where job_id = %s for update", (run,))
    outcome: list[str] = []

    def purge_delete() -> None:
        with psycopg.connect(admin_dsn, autocommit=True) as conn:
            conn.execute("set lock_timeout = '1s'")
            with conn.transaction():
                conn.execute("select set_config('app.purging_case_id', %s, true)", (str(case),))
                try:
                    conn.execute("delete from app.pipeline_batches where id = %s", (batch,))
                    outcome.append("deleted")
                except psycopg.errors.LockNotAvailable:
                    outcome.append("waited")

    worker = threading.Thread(target=purge_delete)
    worker.start()
    worker.join(15)
    holder.rollback()
    holder.close()
    assert outcome == ["waited"]


def test_a_batch_child_is_set_once_and_batches_must_belong_to_the_runs_case(
    admin: psycopg.Connection, seed: Seed
) -> None:
    case, other_case = _case(seed), _case(seed)
    run = _run(admin, case)
    batch = _batch(admin, run, case)
    first, second = _job(admin, case), _job(admin, case)
    admin.execute("update app.pipeline_batches set child_job_id = %s where id = %s", (first, batch))
    admin.execute("update app.pipeline_batches set child_job_id = %s where id = %s", (first, batch))  # same again
    with pytest.raises(psycopg.errors.RaiseException):
        admin.execute("update app.pipeline_batches set child_job_id = %s where id = %s", (second, batch))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        _batch(admin, run, other_case, seq=1)


def test_the_down_file_removes_the_tables_and_the_kind(admin_dsn: str) -> None:
    text = (MIGRATIONS / "down" / "0136_pipeline_page_batches.sql").read_text()
    assert "drop table if exists app.pipeline_batches" in text
    assert "drop table if exists app.pipeline_runs" in text
    assert "read_page_batch" in text
