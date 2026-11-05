"""Migration 0065: `app.rate_limit_hits` and `app.rate_limit_hit()`, the per-person, per-endpoint counter.

One upsert counts a request in the current minute. The table itself is closed to the app roles: a person
cannot read, write or reset their own count, only the function counts, for the signed-in person.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.seed import Seed


def _hit(dsn: str, person: uuid.UUID, endpoint: str) -> tuple[int, int]:
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        row = conn.execute("select hits, retry_after from app.rate_limit_hit(%s)", (endpoint,)).fetchone()
    assert row is not None
    return row


def test_each_call_counts_one_in_the_same_minute(seed: Seed, api_dsn: str) -> None:
    person = seed.person()
    assert [_hit(api_dsn, person, "GET /v1/me")[0] for _ in range(3)] == [1, 2, 3]


def test_retry_after_is_the_seconds_left_in_the_minute(seed: Seed, api_dsn: str) -> None:
    _, retry_after = _hit(api_dsn, seed.person(), "GET /v1/me")
    assert 1 <= retry_after <= 60


def test_endpoints_and_people_count_separately(seed: Seed, api_dsn: str) -> None:
    one, two = seed.person(), seed.person()
    _hit(api_dsn, one, "GET /v1/me")
    _hit(api_dsn, one, "GET /v1/me")
    assert _hit(api_dsn, one, "GET /v1/today")[0] == 1
    assert _hit(api_dsn, two, "GET /v1/me")[0] == 1


def test_a_new_minute_starts_again_at_one(seed: Seed, api_dsn: str, admin: psycopg.Connection) -> None:
    person = seed.person()
    for _ in range(4):
        _hit(api_dsn, person, "GET /v1/me")
    admin.execute(
        "update app.rate_limit_hits set window_start = window_start - interval '1 minute' where person_id = %s",
        (person,),
    )
    assert _hit(api_dsn, person, "GET /v1/me")[0] == 1


def test_nobody_signed_in_is_not_counted(api_dsn: str) -> None:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        rows = conn.execute("select * from app.rate_limit_hit('GET /v1/me')").fetchall()
    assert rows == []


def test_the_app_roles_cannot_read_write_or_reset_the_table(seed: Seed, api_dsn: str, worker_dsn: str) -> None:
    person = seed.person()
    _hit(api_dsn, person, "GET /v1/me")
    for statement in (
        "select * from app.rate_limit_hits",
        "update app.rate_limit_hits set hits = 0",
        "delete from app.rate_limit_hits",
        "insert into app.rate_limit_hits (person_id, endpoint) values (gen_random_uuid(), 'x')",
    ):
        with psycopg.connect(api_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(statement)
    with psycopg.connect(worker_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from app.rate_limit_hit('GET /v1/me')")


def test_the_count_goes_with_the_person(seed: Seed, api_dsn: str, admin: psycopg.Connection) -> None:
    person = seed.person()
    _hit(api_dsn, person, "GET /v1/me")
    admin.execute("delete from app.people where id = %s", (person,))
    assert admin.execute("select count(*) from app.rate_limit_hits where person_id = %s", (person,)).fetchone() == (0,)
