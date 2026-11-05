"""The page-batch tables are in the purge order from the start (ADR 0038).

`pipeline_batches` and `pipeline_runs` (pipeline migration 0136) go first: a batch references its run, and both
reference the case's jobs, so they are removed before every other case table. Until the pipeline creates them the
purge skips them (`purge_case` ignores a listed table that does not exist) and the guard that seeds every case table
does not ask for them (`AWAITING_SLICE_MIGRATION`).
"""

from __future__ import annotations

import psycopg

from app.core.contracts.case_tables import AWAITING_SLICE_MIGRATION, CASE_TABLES, PURGE_ORDER


def test_the_page_batch_tables_are_purged_first() -> None:
    assert PURGE_ORDER[:2] == ("pipeline_batches", "pipeline_runs")
    assert {"pipeline_batches", "pipeline_runs"} <= CASE_TABLES
    assert PURGE_ORDER[-1] == "bundles"


def test_the_tables_wait_for_their_slice_until_they_exist(admin: psycopg.Connection) -> None:
    for table in ("pipeline_batches", "pipeline_runs"):
        exists = admin.execute("select to_regclass(%s)", (f"app.{table}",)).fetchone() != (None,)
        if exists:
            assert table not in AWAITING_SLICE_MIGRATION, "the table exists: remove it from AWAITING_SLICE_MIGRATION"
        else:
            assert AWAITING_SLICE_MIGRATION[table] == "A"


def test_the_database_lists_them_first_too(admin: psycopg.Connection) -> None:
    row = admin.execute("select app.case_purge_order()").fetchone()
    assert row is not None
    assert tuple(row[0][:2]) == ("pipeline_batches", "pipeline_runs")
    assert tuple(row[0]) == PURGE_ORDER
