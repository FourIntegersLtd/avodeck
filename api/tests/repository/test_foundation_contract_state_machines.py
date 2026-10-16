"""The fact, question and report-version state machines in code match docs/spec/state-machines.md,
and the services refuse an undocumented move with 409 `conflict` before the trigger has to."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from app.core.errors import ApiError
from app.core.state_machines import (
    FACT_STATUS_TRANSITIONS,
    QUESTION_STATUS_TRANSITIONS,
    REPORT_VERSION_STATUS_TRANSITIONS,
    change_fact_status,
    change_question_status,
)
from tests.support.contracts import add_fact, add_question
from tests.support.db import scope_for
from tests.support.seed import Seed

FACT_EDGES = {
    ("suggested", "verified"),
    ("suggested", "edited"),
    ("suggested", "rejected"),
    ("verified", "rejected"),
    ("edited", "rejected"),
    ("rejected", "verified"),
}
QUESTION_EDGES = {("suggested", "confirmed"), ("suggested", "edited")}
REPORT_EDGES = {("draft", "locked"), ("locked", "served"), ("served", "withdrawn")}


def code_edges(table: dict[str, frozenset[str]]) -> set[tuple[str, str]]:
    return {(a, b) for a, targets in table.items() for b in targets}


def test_transitions_in_code_are_exactly_the_documented_ones() -> None:
    assert code_edges(FACT_STATUS_TRANSITIONS) == FACT_EDGES
    assert code_edges(QUESTION_STATUS_TRANSITIONS) == QUESTION_EDGES
    assert code_edges(REPORT_VERSION_STATUS_TRANSITIONS) == REPORT_EDGES


@pytest.fixture
def expert_page(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)
    return expert, case, seed.page(case, seed.document(case), 1)


@pytest.mark.anyio
async def test_the_fact_service_refuses_an_undocumented_move_with_409(
    api_db: Database, admin: psycopg.Connection, expert_page: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case, page = expert_page
    fact = add_fact(admin, case, page, status="edited")
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(ApiError) as err:
            await change_fact_status(conn, fact, "verified")
        assert (err.value.status, err.value.code) == (409, "conflict")
    async with api_db.request(scope_for(expert)) as conn:
        await change_fact_status(conn, fact, "rejected")
    assert admin.execute("select status from app.facts where id = %s", (fact,)).fetchone() == ("rejected",)


@pytest.mark.anyio
async def test_the_question_service_refuses_an_undocumented_move_with_409(
    api_db: Database, admin: psycopg.Connection, expert_page: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    expert, case, _ = expert_page
    question = add_question(admin, case, status="confirmed")
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(ApiError) as err:
            await change_question_status(conn, question, "edited")
        assert err.value.status == 409
