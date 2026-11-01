"""ADR 0034 (Decision B): only a role in `cases.verification_duty_roles` may check a fact.

The trigger is dynamic and independent of the static `fact.verify` cell in the permissions matrix.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.facts_seed import insert_fact
from tests.support.seed import Seed

Refused = psycopg.errors.InsufficientPrivilege


@pytest.fixture
def case_with_fact(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    case = seed.case(org, expert, members={assistant: "assistant"})
    page = seed.page(case, seed.document(case), 1)
    return {"expert": expert, "assistant": assistant, "case": case, "page": page}


def verify_as(api_dsn: str, person: uuid.UUID, fact: uuid.UUID) -> int:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        return conn.execute("update app.facts set status = 'verified' where id = %s", (fact,)).rowcount


def test_a_new_case_defaults_to_the_named_expert_only(
    admin: psycopg.Connection, case_with_fact: dict[str, uuid.UUID]
) -> None:
    row = admin.execute(
        "select verification_duty_roles from app.cases where id = %s", (case_with_fact["case"],)
    ).fetchone()
    assert row == (["named_expert"],)


def test_the_named_expert_may_verify_under_the_default(
    api_dsn: str, admin: psycopg.Connection, case_with_fact: dict[str, uuid.UUID]
) -> None:
    w = case_with_fact
    fact = insert_fact(admin, w["case"], w["page"], "note", status="suggested")
    assert verify_as(api_dsn, w["expert"], fact) == 1


def test_a_role_without_the_duty_cannot_verify(
    api_dsn: str, admin: psycopg.Connection, case_with_fact: dict[str, uuid.UUID]
) -> None:
    w = case_with_fact
    fact = insert_fact(admin, w["case"], w["page"], "note", status="suggested")
    try:
        changed = verify_as(api_dsn, w["assistant"], fact)
    except Refused:
        return
    assert changed == 0


def test_the_trigger_blocks_the_named_expert_when_the_case_does_not_list_the_role(
    api_dsn: str, admin: psycopg.Connection, case_with_fact: dict[str, uuid.UUID]
) -> None:
    w = case_with_fact
    fact = insert_fact(admin, w["case"], w["page"], "note", status="suggested")
    admin.execute("update app.cases set verification_duty_roles = '{co_expert}' where id = %s", (w["case"],))
    with pytest.raises(Refused):
        verify_as(api_dsn, w["expert"], fact)
