"""Migration 0017: the pipeline's extra job kinds say which case action queues them (rule 9)."""

from __future__ import annotations

import psycopg


def test_scan_and_reprocess_jobs_need_the_upload_action(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select kind, action from app.job_kind_actions where kind in ('scan_document', 'reprocess_case') order by kind"
    ).fetchall()
    assert rows == [("reprocess_case", "case.upload"), ("scan_document", "case.upload")]


def test_uploads_say_whether_they_add_later_records(admin: psycopg.Connection) -> None:
    row = admin.execute(
        "select data_type, is_nullable, column_default from information_schema.columns"
        " where table_schema = 'app' and table_name = 'uploads' and column_name = 'append'"
    ).fetchone()
    assert row == ("boolean", "NO", "false")
