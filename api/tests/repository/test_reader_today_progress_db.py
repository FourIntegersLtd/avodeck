"""Today's "pages to read" checks every case in one go (not one query per case)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.contracts.today import CaseCard, needs_you
from app.core.db import Database, statement_count
from app.features.reader import repo
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def card(case: uuid.UUID, org: uuid.UUID) -> CaseCard:
    return CaseCard(
        case_id=case, label="x", organisation_id=org, organisation_name=None, organisation_kind=None,
        status="active", stage=None, due_date=None, party_role=None, jurisdiction="j", case_type="c",
        specialty="s", your_role="named_expert", on_hold=False,
    )  # fmt: skip


def world(seed: Seed, n: int) -> tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    cases = []
    for i in range(n):
        case = seed.case(org, expert, status="active")
        doc = seed.document(case)
        pages = [seed.page(case, doc, k + 1) for k in range(i + 2)]
        seed.admin.execute(
            "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 12)",
            (case, pages[0], expert),
        )
        cases.append(case)
    return expert, org, cases


async def statements(api_db: Database, expert: uuid.UUID, cards: list[CaseCard]) -> int:
    provider = needs_you.providers["reader_pages_to_read"]
    counter = [0]
    token = statement_count.set(counter)
    try:
        async with api_db.request(scope_for(expert)) as conn:
            counter[0] = 0
            await provider(conn, cards)
            return counter[0]
    finally:
        statement_count.reset(token)


async def test_one_case_and_five_cases_run_the_same_number_of_statements(api_db: Database, seed: Seed) -> None:
    expert, org, cases = world(seed, 5)
    one = await statements(api_db, expert, [card(cases[0], org)])
    five = await statements(api_db, expert, [card(c, org) for c in cases])
    assert one == five


async def test_progress_many_equals_progress_for_each_case(api_db: Database, seed: Seed) -> None:
    expert, _org, cases = world(seed, 4)
    async with api_db.request(scope_for(expert)) as conn:
        many: dict[Any, Any] = await repo.progress_many(conn, cases, expert)
        for case in cases:
            assert many[case] == await repo.progress(conn, case, expert)
