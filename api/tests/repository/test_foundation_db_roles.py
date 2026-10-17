"""Migration 0000: the `app` schema, the restricted roles and the session helpers (ADR 0013)."""

from __future__ import annotations

import psycopg
import pytest

RESTRICTED = ("app_api", "app_worker", "app_migrator")


@pytest.mark.parametrize("role", RESTRICTED)
def test_restricted_role_exists_without_superuser_or_bypassrls(admin: psycopg.Connection, role: str) -> None:
    row = admin.execute(
        "select rolsuper, rolbypassrls, rolcreaterole, rolcreatedb from pg_roles where rolname = %s", (role,)
    ).fetchone()
    assert row is not None, f"{role} missing"
    assert row == (False, False, False, False)


def test_app_schema_exists_and_is_owned_by_the_migrator(admin: psycopg.Connection) -> None:
    row = admin.execute("select pg_get_userbyid(nspowner) from pg_namespace where nspname = 'app'").fetchone()
    assert row == ("app_migrator",)


@pytest.mark.parametrize("role", ["app_api", "app_worker"])
def test_runtime_roles_can_use_the_app_schema_but_not_create_in_it(admin: psycopg.Connection, role: str) -> None:
    usage, create = admin.execute(
        "select has_schema_privilege(%s, 'app', 'USAGE'), has_schema_privilege(%s, 'app', 'CREATE')", (role, role)
    ).fetchone()  # type: ignore[misc]
    assert usage is True
    assert create is False


@pytest.mark.parametrize("role", ["anon", "authenticated", "service_role"])
def test_supabase_data_api_roles_cannot_reach_the_app_schema(admin: psycopg.Connection, role: str) -> None:
    (usage,) = admin.execute("select has_schema_privilege(%s, 'app', 'USAGE')", (role,)).fetchone()  # type: ignore[misc]
    assert usage is False


@pytest.mark.parametrize("role", ["app_api", "app_worker"])
def test_runtime_roles_own_nothing(admin: psycopg.Connection, role: str) -> None:
    (owned,) = admin.execute(
        "select count(*) from pg_class where relowner = (select oid from pg_roles where rolname = %s)", (role,)
    ).fetchone()  # type: ignore[misc]
    assert owned == 0


@pytest.mark.parametrize("role", ["app_api", "app_worker"])
def test_runtime_roles_are_not_members_of_privileged_roles(admin: psycopg.Connection, role: str) -> None:
    rows = admin.execute(
        """
        select r.rolname from pg_auth_members m
        join pg_roles r on r.oid = m.roleid
        where m.member = (select oid from pg_roles where rolname = %s)
        """,
        (role,),
    ).fetchall()
    assert rows == []


def test_session_helpers_return_nothing_when_unset(admin: psycopg.Connection) -> None:
    row = admin.execute("select app.current_person_id(), app.current_org_ids(), app.current_job_case_id()").fetchone()
    assert row == (None, [], None)


def test_new_app_tables_are_readable_by_runtime_roles_by_default(admin: psycopg.Connection) -> None:
    admin.execute("create table app.zz_default_grants_probe (id int)")
    try:
        for role in ("app_api", "app_worker"):
            (ok,) = admin.execute(
                "select has_table_privilege(%s, 'app.zz_default_grants_probe', 'SELECT,INSERT,UPDATE,DELETE')",
                (role,),
            ).fetchone()  # type: ignore[misc]
            assert ok is True, role
    finally:
        admin.execute("drop table app.zz_default_grants_probe")
