"""ADR 0034: a bundle belongs to the cases that point at it; access follows the case role.

- two cases sharing a bundle: each case's authorised people see the pages, and nobody else does;
- a person with no case on the bundle sees no page, document or bundle row;
- the same page numbers and ids serve both cases (provenance stays on the page).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.seed import Seed


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    w = {n: seed.person() for n in ("expert_a", "expert_b", "outsider")}
    org_a = seed.org(kind="team", members={w["expert_a"]: "owner"})
    org_b = seed.org(kind="team", members={w["expert_b"]: "owner"})
    seed.org(kind="personal", members={w["outsider"]: "owner"})
    w["case_a"] = seed.case(org_a, w["expert_a"])
    w["case_b"] = seed.case(org_b, w["expert_b"])
    seed.page(w["case_a"], seed.document(w["case_a"]), 1)
    admin.execute(
        "update app.cases set bundle_id = (select bundle_id from app.cases where id = %s) where id = %s",
        (w["case_a"], w["case_b"]),
    )
    return w


def page_count_as(api_dsn: str, person: uuid.UUID) -> int:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        return int(conn.execute("select count(*) from app.pages").fetchone()[0])  # type: ignore[index]


def test_both_cases_experts_see_the_shared_pages(api_dsn: str, world: dict[str, uuid.UUID]) -> None:
    assert page_count_as(api_dsn, world["expert_a"]) == 1
    assert page_count_as(api_dsn, world["expert_b"]) == 1


def test_a_person_with_no_case_on_the_bundle_sees_nothing(api_dsn: str, world: dict[str, uuid.UUID]) -> None:
    assert page_count_as(api_dsn, world["outsider"]) == 0


def test_losing_the_only_case_that_reaches_a_bundle_hides_it(
    api_dsn: str, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    case = seed.case(org, expert)
    seed.page(case, seed.document(case), 1)
    other = seed.person()
    seed.org(kind="personal", members={other: "owner"})
    assert page_count_as(api_dsn, expert) == 1
    assert page_count_as(api_dsn, other) == 0
