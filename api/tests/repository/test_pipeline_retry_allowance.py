"""Slice A: a paused retry does not offer a button that cannot work.

When the cost guard paused the run and the case is still over its processing allowance (the guard's own formula:
tokens used against `AI_COST_GUARD_MULTIPLE` x `AI_EXPECTED_TOKENS_PER_PAGE` x the larger of the case's pages and
`AI_MIN_EXPECTED_PAGES`), the summary says so (`retry.allowance_used_up`) and the screens show a plain line and no
button. When the allowance has room again the button is back. Counts and codes only (rule 6).
"""

# ruff: noqa: F811 (the fixtures are imported, then named by each test)
from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from tests.repository.test_pipeline_retry_paused import paused_retry, retry_of
from tests.repository.test_pipeline_retry_unread import world  # noqa: F401

pytestmark = pytest.mark.anyio


def use_tokens(admin: psycopg.Connection, case: uuid.UUID, tokens: int) -> None:
    admin.execute(
        "insert into app.ai_calls (case_id, task_id, model_id, region, prompt_version, input_tokens, output_tokens,"
        " outcome) values (%s, 'zz_task', 'zz-model', 'zz', 'v1', %s, 0, 'ok')",
        (case, tokens),
    )


@pytest.fixture
def tight(monkeypatch: pytest.MonkeyPatch) -> None:
    """The world has 4 pages: with these settings the allowance is 1 x 10 x max(4, 1) = 40 tokens."""
    monkeypatch.setenv("AI_EXPECTED_TOKENS_PER_PAGE", "10")
    monkeypatch.setenv("AI_MIN_EXPECTED_PAGES", "1")
    monkeypatch.setenv("AI_COST_GUARD_MULTIPLE", "1")


async def test_a_run_the_cost_guard_paused_says_the_allowance_is_used_up_while_the_case_is_over_it(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any, tight: None
) -> None:
    client = await client_for(world["expert"])
    paused_retry(admin, world["case"], "cost_guard")
    use_tokens(admin, world["case"], 39)
    assert (await retry_of(client, world["case"]))["allowance_used_up"] is False, "39 of 40: room left"
    use_tokens(admin, world["case"], 1)
    got = await retry_of(client, world["case"])
    assert (got["paused"], got["pause_reason"], got["allowance_used_up"]) == (True, "cost_guard", True)


async def test_the_button_comes_back_when_the_allowance_has_room_again(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any, tight: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = await client_for(world["expert"])
    paused_retry(admin, world["case"], "cost_guard")
    use_tokens(admin, world["case"], 100)
    assert (await retry_of(client, world["case"]))["allowance_used_up"] is True
    monkeypatch.setenv("AI_COST_GUARD_MULTIPLE", "3")  # 120 tokens now
    assert (await retry_of(client, world["case"]))["allowance_used_up"] is False


async def test_the_allowance_is_only_reported_for_a_paused_run_the_guard_held_back(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any, tight: None
) -> None:
    client = await client_for(world["expert"])
    case = world["case"]
    use_tokens(admin, case, 1000)
    job = paused_retry(admin, case, "key_service_unreachable")
    assert (await retry_of(client, case))["allowance_used_up"] is False, "another pause is not the allowance"
    admin.execute("update app.jobs set status = 'running', error_code = null where id = %s", (job,))
    assert (await retry_of(client, case))["allowance_used_up"] is False, "a run that is moving is not held back"
