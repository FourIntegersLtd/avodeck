"""Rule 9 (ADR 0013): row-level security cannot be bypassed by the running app.

- every table in the `app` schema has RLS enabled;
- the roles the app runs as never bypass RLS and are never superusers;
- the `app` schema is not exposed through Supabase's Data API.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import psycopg

REPO = Path(__file__).resolve().parents[3]


def test_every_app_table_has_row_level_security(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        """
        select c.relname from pg_class c
        join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'app' and c.relkind in ('r', 'p') and not c.relrowsecurity
        """
    ).fetchall()
    assert rows == [], f"tables without RLS: {[r[0] for r in rows]}"


def test_runtime_roles_never_bypass_rls(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select rolname, rolsuper, rolbypassrls from pg_roles where rolname in ('app_api', 'app_worker')"
    ).fetchall()
    assert sorted(rows) == [("app_api", False, False), ("app_worker", False, False)]


def test_app_schema_is_not_exposed_through_the_data_api() -> None:
    config = tomllib.loads((REPO / "supabase" / "config.toml").read_text())
    assert "app" not in config["api"]["schemas"]
    assert "app" not in config["api"].get("extra_search_path", [])
