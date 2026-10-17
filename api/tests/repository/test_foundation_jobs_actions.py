"""Rule 9 for jobs: the database decides which case action a job kind needs (`app.job_kind_actions`).

The caller no longer names the action: `app.enqueue_job()` looks it up from the kind, so an
assistant (who may upload) cannot queue an export or a deletion, and nobody can call the function
with an action of their choosing. Kinds not listed need `case.upload`.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from app.core.jobs import JobContext, JobRegistry
from tests.support.db import scope_for
from tests.support.jobs import isolated_registry
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant"})
    return {"expert": expert, "assistant": assistant, "case": case}


def registry_with(*kinds: str) -> JobRegistry:
    registry = isolated_registry()
    for kind in kinds:

        async def never(ctx: JobContext) -> None:  # pragma: no cover - never runs
            raise AssertionError

        registry.kind(kind)(never)
    return registry


@pytest.mark.parametrize("kind", ["export_case", "delete_case", "build_report"])
async def test_the_kind_sets_the_action_an_assistant_may_not_have(
    kind: str, api_db: Database, team: dict[str, uuid.UUID]
) -> None:
    registry = registry_with(kind)
    if kind == "build_report":  # report.build: assistants may
        async with api_db.request(scope_for(team["assistant"])) as conn:
            assert await registry.enqueue(conn, team["case"], kind)
        return
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.request(scope_for(team["assistant"])) as conn:
            await registry.enqueue(conn, team["case"], kind)
    async with api_db.request(scope_for(team["expert"])) as conn:
        assert await registry.enqueue(conn, team["case"], kind)


async def test_nobody_can_name_the_action_themselves(api_dsn: str, team: dict[str, uuid.UUID]) -> None:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(team["assistant"]),))
        with pytest.raises(psycopg.errors.UndefinedFunction):
            conn.execute(
                "select app.enqueue_job(%s, 'export_case', 'default', 0, false, 'case.upload', null)",
                (team["case"],),
            )


def test_enqueue_takes_no_action_argument() -> None:
    import inspect

    assert "action" not in inspect.signature(JobRegistry.enqueue).parameters
