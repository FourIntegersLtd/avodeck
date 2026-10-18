"""What slice D (Understand) needs from the database (migration 0023, permissions.md).

- `app.job_kind_actions`: suggesting links needs `link.suggest`; running the checks again needs
  `finding.decide` (only the named expert decides findings).
- `app.set_fact_availability(fact, available_at, basis)` writes when an entry reached the clinician. The
  worker may, inside the fact's case's own job; a person only with `availability.override` (the named
  expert). It changes `available_at` and `availability_basis` only: no new fact version, no status change.
- `tests.support.facts_seed.insert_fact()` seeds facts for slices that read them.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

import psycopg
import pytest

from tests.support.facts_seed import insert_fact
from tests.support.seed import Seed

WHEN = datetime(2024, 3, 31, 8, 2, tzinfo=UTC)


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "co_expert", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "owner", p["assistant"]: "assistant", p["co_expert"]: "expert"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["assistant"]: "assistant", p["co_expert"]: "co_expert"})
    p["other_case"] = seed.case(seed.org(kind="personal", members={p["outsider"]: "admin"}), p["outsider"])
    p["page"] = seed.page(p["case"], seed.document(p["case"]), 1)
    return p


def as_person(dsn: str, person: uuid.UUID) -> psycopg.Connection:
    conn = psycopg.connect(dsn)
    conn.execute("select set_config('app.person_id', %s, false)", (str(person),))
    return conn


def as_job(dsn: str, case: uuid.UUID) -> psycopg.Connection:
    conn = psycopg.connect(dsn)
    conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
    return conn


def fact_row(admin: psycopg.Connection, fact: uuid.UUID) -> tuple[object, ...] | None:
    return admin.execute(
        "select available_at, availability_basis, version, status, value from app.facts where id = %s", (fact,)
    ).fetchone()


def a_fact(admin: psycopg.Connection, world: dict[str, uuid.UUID]) -> uuid.UUID:
    return insert_fact(
        admin, world["case"], world["page"], "measurement", {"name": "zz"}, "9", ["w1", "w2"], date(2024, 3, 30)
    )


# --- job kinds ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("kind", "action"), [("suggest_links", "link.suggest"), ("rerun_checks", "finding.decide")])
def test_understand_job_kinds_need_their_actions(admin: psycopg.Connection, kind: str, action: str) -> None:
    row = admin.execute("select action from app.job_kind_actions where kind = %s", (kind,)).fetchone()
    assert row == (action,)


# --- the seed helper ------------------------------------------------------------------------------


def test_insert_fact_seeds_a_verified_fact_with_its_fields(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    fact = a_fact(admin, world)
    sql = "select kind, fields, value, word_ids, event_date, status from app.facts where id = %s"
    row = admin.execute(sql, (fact,))
    assert row.fetchone() == ("measurement", {"name": "zz"}, "9", ["w1", "w2"], date(2024, 3, 30), "verified")


# --- app.set_fact_availability ----------------------------------------------------------------------


def test_the_worker_sets_availability_in_its_own_case_without_a_new_version(
    admin: psycopg.Connection, worker_dsn: str, world: dict[str, uuid.UUID]
) -> None:
    fact = a_fact(admin, world)
    before = fact_row(admin, fact)
    assert before is not None
    with as_job(worker_dsn, world["case"]) as conn:
        conn.execute("select app.set_fact_availability(%s, %s, 'own_record')", (fact, WHEN))
    assert fact_row(admin, fact) == (WHEN, "own_record", before[2], "verified", "9")


def test_the_worker_cannot_set_availability_in_another_case(
    admin: psycopg.Connection, worker_dsn: str, world: dict[str, uuid.UUID]
) -> None:
    fact = a_fact(admin, world)
    with as_job(worker_dsn, world["other_case"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select app.set_fact_availability(%s, %s, 'own_record')", (fact, WHEN))
    assert fact_row(admin, fact)[:2] == (None, None)  # type: ignore[index]


def test_the_named_expert_overrides_availability(
    admin: psycopg.Connection, api_dsn: str, world: dict[str, uuid.UUID]
) -> None:
    fact = a_fact(admin, world)
    version = fact_row(admin, fact)[2]  # type: ignore[index]
    with as_person(api_dsn, world["expert"]) as conn:
        conn.execute("select app.set_fact_availability(%s, %s, 'expert_override')", (fact, WHEN))
    assert fact_row(admin, fact)[:3] == (WHEN, "expert_override", version)  # type: ignore[index]


def test_the_named_expert_can_clear_availability(
    admin: psycopg.Connection, api_dsn: str, world: dict[str, uuid.UUID]
) -> None:
    fact = insert_fact(
        admin, world["case"], world["page"], "measurement", {}, "9", available_at=WHEN, availability_basis="own_record"
    )
    with as_person(api_dsn, world["expert"]) as conn:
        conn.execute("select app.set_fact_availability(%s, null, 'expert_unknown')", (fact,))
    assert fact_row(admin, fact)[:2] == (None, "expert_unknown")  # type: ignore[index]


@pytest.mark.parametrize("who", ["assistant", "co_expert", "outsider"])
def test_anyone_else_is_refused(admin: psycopg.Connection, api_dsn: str, world: dict[str, uuid.UUID], who: str) -> None:
    fact = a_fact(admin, world)
    with as_person(api_dsn, world[who]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select app.set_fact_availability(%s, %s, 'expert_override')", (fact, WHEN))
    assert fact_row(admin, fact)[:2] == (None, None)  # type: ignore[index]


def test_nobody_signed_in_and_no_job_is_refused(
    admin: psycopg.Connection, api_dsn: str, world: dict[str, uuid.UUID]
) -> None:
    fact = a_fact(admin, world)
    with psycopg.connect(api_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select app.set_fact_availability(%s, %s, 'own_record')", (fact, WHEN))


def test_an_unknown_fact_is_refused(worker_dsn: str, world: dict[str, uuid.UUID]) -> None:
    with as_job(worker_dsn, world["case"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select app.set_fact_availability(%s, %s, 'own_record')", (uuid.uuid4(), WHEN))


def test_the_basis_must_be_a_short_code(
    admin: psycopg.Connection, worker_dsn: str, world: dict[str, uuid.UUID]
) -> None:
    fact = a_fact(admin, world)
    with as_job(worker_dsn, world["case"]) as conn, pytest.raises(psycopg.errors.CheckViolation):
        conn.execute("select app.set_fact_availability(%s, %s, 'Not A Code')", (fact, WHEN))


def test_any_other_change_to_a_fact_still_raises_its_version(
    admin: psycopg.Connection, worker_dsn: str, world: dict[str, uuid.UUID]
) -> None:
    fact = a_fact(admin, world)
    version = fact_row(admin, fact)[2]  # type: ignore[index]
    with as_job(worker_dsn, world["case"]) as conn:
        conn.execute("update app.facts set available_at = %s, value = '10' where id = %s", (WHEN, fact))
    assert fact_row(admin, fact)[2] == version + 1  # type: ignore[index, operator]
    with as_job(worker_dsn, world["case"]) as conn:
        conn.execute("update app.facts set available_at = %s where id = %s", (None, fact))
    assert fact_row(admin, fact)[2] == version + 2  # type: ignore[index, operator]
