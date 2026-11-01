"""ADR 0034: two cases may point at one bundle and keep their own readings of it.

- the bundle's pages keep one set of numbers and ids for both cases;
- each case keeps its own facts on those pages (facts carry the case, pages carry the bundle);
- a fact cannot cite a page of some other bundle;
- a page's bundle cannot be changed once set.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.facts_seed import insert_fact
from tests.support.seed import Seed


@pytest.fixture
def shared(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert_a, expert_b = seed.person(), seed.person()
    org_a = seed.org(kind="team", members={expert_a: "owner"})
    org_b = seed.org(kind="team", members={expert_b: "owner"})
    w: dict[str, uuid.UUID] = {"case_a": seed.case(org_a, expert_a), "case_b": seed.case(org_b, expert_b)}
    w["page"] = seed.page(w["case_a"], seed.document(w["case_a"]), 1)
    admin.execute(
        "update app.cases set bundle_id = (select bundle_id from app.cases where id = %s) where id = %s",
        (w["case_a"], w["case_b"]),
    )
    return w


def test_both_cases_point_at_the_same_bundle_and_page(admin: psycopg.Connection, shared: dict[str, uuid.UUID]) -> None:
    rows = admin.execute(
        "select count(distinct bundle_id) from app.cases where id in (%s, %s)", (shared["case_a"], shared["case_b"])
    ).fetchone()
    assert rows == (1,)


def test_each_case_keeps_its_own_facts_on_the_shared_page(
    admin: psycopg.Connection, shared: dict[str, uuid.UUID]
) -> None:
    insert_fact(admin, shared["case_a"], shared["page"], "note", status="suggested")
    insert_fact(admin, shared["case_b"], shared["page"], "note", status="suggested")
    for case in ("case_a", "case_b"):
        n = admin.execute("select count(*) from app.facts where case_id = %s", (shared[case],)).fetchone()
        assert n == (1,)


def test_a_fact_cannot_cite_a_page_of_another_bundle(admin: psycopg.Connection, seed: Seed) -> None:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    one, two = seed.case(org, expert), seed.case(org, expert)
    page_of_two = seed.page(two, seed.document(two), 1)
    with pytest.raises(psycopg.Error):
        insert_fact(admin, one, page_of_two, "note", status="suggested")


def test_a_pages_bundle_cannot_be_changed(admin: psycopg.Connection, seed: Seed, shared: dict[str, uuid.UUID]) -> None:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    other = seed.case(org, expert)
    with pytest.raises(psycopg.Error):
        admin.execute(
            "update app.pages set bundle_id = (select bundle_id from app.cases where id = %s) where id = %s",
            (other, shared["page"]),
        )
