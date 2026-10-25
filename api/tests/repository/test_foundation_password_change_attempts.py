"""Migration 0036: `password_change_attempts`, wrong current passwords per person (ADR 0024).

The person's own row only (row-level security); the API role may not delete it (a lock cannot be wiped by
the app) and the worker cannot see it.
"""

from __future__ import annotations

import psycopg
import pytest

from tests.support.seed import Seed


def test_only_the_person_sees_their_row_and_nobody_deletes_it(
    seed: Seed, api_dsn: str, worker_dsn: str, admin: psycopg.Connection
) -> None:
    person, other = seed.person(), seed.person()
    admin.execute("insert into app.password_change_attempts (person_id, failures) values (%s, 3)", (person,))
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(other),))
        assert conn.execute("select count(*) from app.password_change_attempts").fetchone() == (0,)
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        assert conn.execute("select failures from app.password_change_attempts").fetchall() == [(3,)]
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from app.password_change_attempts where person_id = %s", (person,))
    with psycopg.connect(worker_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from app.password_change_attempts")
