"""All timestamps are stored and handled in UTC (non-functional requirements; architecture 10.9)."""

from __future__ import annotations

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed


def test_no_app_column_stores_a_time_without_its_time_zone(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        """
        select table_name, column_name from information_schema.columns
        where table_schema = 'app' and data_type in ('timestamp without time zone', 'time without time zone')
        """
    ).fetchall()
    assert rows == []


def test_core_tables_record_when_rows_were_made(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        """
        select t.table_name from information_schema.tables t
        where t.table_schema = 'app' and t.table_type = 'BASE TABLE'
          and not exists (
            select 1 from information_schema.columns c
            where c.table_schema = 'app' and c.table_name = t.table_name
              and c.column_name in ('created_at', 'at', 'added_at', 'started_at')
              and c.data_type = 'timestamp with time zone')
        """
    ).fetchall()
    assert rows == []


@pytest.mark.anyio
async def test_every_request_transaction_runs_in_utc(api_db: Database, seed: Seed) -> None:
    async with api_db.request(scope_for(seed.person())) as conn:
        cur = await conn.execute("select current_setting('TimeZone'), now()")
        tz, now = await cur.fetchone()  # type: ignore[misc]
    assert tz == "UTC"
    assert now.utcoffset().total_seconds() == 0
