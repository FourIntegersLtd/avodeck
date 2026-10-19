"""GET /v1/cases/{case_id}/steps/progress: one entry per pack step with done, total and percent.

What each step counts is named by the pack step's `meters`, so the same code serves any pack (rule 11).
Run under the launch and the fixture packs.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def step_with(combo: Combo, meter: str) -> str:
    steps = combo.raw("case-type", "workflow.yaml")["steps"]
    return str(next(s["key"] for s in steps if meter in s.get("meters", [])))


async def progress(client_for: ClientFor, person: uuid.UUID, case: uuid.UUID) -> dict[str, dict[str, Any]]:
    resp = await (await client_for(person)).get(f"/v1/cases/{case}/steps/progress")
    assert resp.status_code == 200, resp.text
    return {s["key"]: s for s in resp.json()["steps"]}


def case_for(seed: Seed, combo: Combo) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, packs=combo.case_row())
    return expert, case


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.case_type)
async def test_one_entry_per_pack_step_and_nothing_to_count_is_null(
    client_for: ClientFor, seed: Seed, combo: Combo
) -> None:
    expert, case = case_for(seed, combo)
    got = await progress(client_for, expert, case)
    steps = combo.raw("case-type", "workflow.yaml")["steps"]
    assert list(got) == [s["key"] for s in steps]
    for step in steps:
        if not step.get("meters"):
            assert got[step["key"]] == {"key": step["key"], "done": None, "total": None, "percent": None}
    # a new case has nothing to count yet: a total of 0 has no percent
    reading = got[step_with(combo, "pages_read")]
    assert (reading["done"], reading["total"], reading["percent"]) == (0, 0, None)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.case_type)
async def test_reading_counts_pages_read_of_pages_not_quarantined(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, combo: Combo
) -> None:
    expert, case = case_for(seed, combo)
    doc = seed.document(case)
    pages = [seed.page(case, doc, n) for n in range(1, 5)]
    admin.execute("update app.pages set quarantined = true, quarantine_reason = 'x' where id = %s", (pages[3],))
    admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 5)",
        (case, pages[0], expert),
    )
    admin.commit()
    reading = (await progress(client_for, expert, case))[step_with(combo, "pages_read")]
    assert (reading["done"], reading["total"], reading["percent"]) == (1, 3, 33)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.case_type)
async def test_questions_and_report_stages_are_counted(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, combo: Combo
) -> None:
    expert, case = case_for(seed, combo)
    for n in (1, 2):
        admin.execute(
            "insert into app.questions (case_id, number, text_verbatim, kind) values (%s, %s, 'q', 'x')", (case, n)
        )
    admin.execute("update app.questions set status = 'confirmed' where case_id = %s and number = 1", (case,))
    admin.execute(
        "insert into app.report_versions (case_id, version_no, type, display_name) values (%s, 1, 'initial', 'r')",
        (case,),
    )
    admin.commit()
    got = await progress(client_for, expert, case)
    prepare = got[step_with(combo, "questions_confirmed")]
    assert (prepare["done"], prepare["total"], prepare["percent"]) == (1, 2, 50)
    write = got[step_with(combo, "report_stages")]
    # one question confirmed and no opinion yet (0 of 1), plus a report built (1 of 4 stages)
    assert (write["done"], write["total"], write["percent"]) == (1, 5, 20)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.case_type)
async def test_a_stranger_gets_404(client_for: ClientFor, seed: Seed, combo: Combo) -> None:
    _, case = case_for(seed, combo)
    resp = await (await client_for(seed.person())).get(f"/v1/cases/{case}/steps/progress")
    assert resp.status_code == 404
