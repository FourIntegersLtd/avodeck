"""AiKit sends each task the pack context for that task (`pack.prompt_context(task.task_id)`)."""

from __future__ import annotations

import pytest

from app.core.ai.kit import AiKit, AiTask
from app.core.db import Database
from app.core.packs import default_library
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.db import scope_for
from tests.support.packs import FIXTURE
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SCHEMA = {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]}


@pytest.mark.parametrize("task_id", ["T-02", "T-04", "T-06", "T-07b"])
async def test_the_kit_sends_the_context_for_the_task(task_id: str, api_db: Database, seed: Seed) -> None:
    expert = seed.person()
    seed.org(members={expert: "owner"})
    pack = default_library().combine(*FIXTURE.codes)
    model = ScriptedBedrock([response({"n": 1})])
    task = AiTask(task_id=task_id, prompt_version="2026-10-17.1", model="sonnet", system="Count.", schema=SCHEMA)
    async with api_db.request(scope_for(expert)) as conn:
        await AiKit(cell(), model).run(conn, task, pack=pack, text="nothing here")
    [sent] = model.requests
    assert sent.context == pack.prompt_context(task_id)
