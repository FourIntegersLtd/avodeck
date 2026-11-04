"""Slice A: "Try again" is refused while the case is over its processing allowance (F11).

The screens hide the button then, but the server does not rely on them: a press or a direct call while the case
is over the cost guard's allowance (`cost_guard()`, the same numbers the worker trips on) answers 409
`allowance_used_up` and changes nothing: the run stays paused, no queue row is made, and no second job is
queued. Once the allowance has room the same press hands the run back. Counts and codes only (rule 6).
"""

# ruff: noqa: F811 (the fixtures are imported, then named by each test)
from __future__ import annotations

from typing import Any

import psycopg
import pytest

from tests.repository.test_pipeline_retry_allowance import tight, use_tokens  # noqa: F401
from tests.repository.test_pipeline_retry_paused import paused_retry, queue_rows, state
from tests.repository.test_pipeline_retry_unread import RETRY, world  # noqa: F401

pytestmark = pytest.mark.anyio


async def press(client: Any, case: Any) -> Any:
    return await client.post(f"/v1/cases/{case}/transcript/retry")


@pytest.mark.parametrize("reason", ["cost_guard", "service_busy", "key_service_unreachable"])
async def test_a_press_while_the_case_is_over_its_allowance_is_refused_and_changes_nothing(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any, tight: None, reason: str
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    job = paused_retry(admin, case, reason)
    use_tokens(admin, case, 40)  # the allowance is 40 tokens
    for _ in range(2):
        resp = await press(client, case)
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "allowance_used_up"
    assert state(admin, job) == ("paused", reason), "the run is left as it was"
    assert queue_rows(admin, job) == 0, "no queue row for a run that would pause again"
    jobs = admin.execute("select count(*) from app.jobs where case_id = %s and kind = %s", (case, RETRY)).fetchone()
    assert jobs == (1,), "and no second job"


async def test_the_same_press_hands_the_run_back_once_the_allowance_has_room(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any, tight: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    job = paused_retry(admin, case, "cost_guard")
    use_tokens(admin, case, 40)
    assert (await press(client, case)).status_code == 409
    monkeypatch.setenv("AI_COST_GUARD_MULTIPLE", "3")  # 120 tokens now
    resp = await press(client, case)
    assert resp.status_code == 202 and resp.json()["status"] == "preparing"
    assert state(admin, job) == ("paused", "resuming") and queue_rows(admin, job) == 1


async def test_a_case_one_token_under_its_allowance_is_still_handed_back(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any, tight: None
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    job = paused_retry(admin, case, "cost_guard")
    use_tokens(admin, case, 39)
    assert (await press(client, case)).status_code == 202
    assert state(admin, job) == ("paused", "resuming")


async def test_a_case_over_its_allowance_with_no_unread_pages_is_not_refused(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any, tight: None
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    use_tokens(admin, case, 1000)
    admin.execute(
        "update app.page_analysis set entries_failed_at = null where page_id in"
        " (select id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s))",
        (case,),
    )
    assert (await press(client, case)).status_code != 409, "nothing to try again, so nothing to refuse"
