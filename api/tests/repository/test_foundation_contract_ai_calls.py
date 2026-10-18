"""The methodology appendix's AI summary (`app.core.contracts.ai_calls`; request from slice F).

`ai_call_summary(conn, case_id)` groups the case's `ai_calls` metadata by task, model ID, region and
prompt version, with the number of calls and the first and last call time. No prompt or response
text exists to return (rule 6). Row-level security on `ai_calls` already lets anyone who may see the
case's content, and the case's own job, read them: an outsider and a job on another case get nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import fields
from datetime import UTC, datetime

import psycopg
import pytest

from app.core.contracts.ai_calls import AiCallSummary, ai_call_summary
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def at(day: int) -> datetime:
    return datetime(2026, 3, day, 9, 0, tzinfo=UTC)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    w = {"expert": seed.person(), "outsider": seed.person(), "other": seed.person()}
    seed.org(kind="personal", members={w["outsider"]: "owner"})
    w["case"] = seed.case(seed.org(kind="personal", members={w["expert"]: "owner"}), w["expert"], status="active")
    w["elsewhere"] = seed.case(seed.org(kind="personal", members={w["other"]: "owner"}), w["other"], status="active")
    rows = [
        (w["case"], "T-05", "model-a", "zz", "v1", at(3)),
        (w["case"], "T-05", "model-a", "zz", "v1", at(1)),
        (w["case"], "T-05", "model-a", "zz", "v2", at(5)),
        (w["case"], "T-02", "model-b", "zz", "v1", at(2)),
        (w["elsewhere"], "T-05", "model-a", "zz", "v1", at(4)),
    ]
    for case, task, model, region, version, when in rows:
        admin.execute(
            "insert into app.ai_calls (case_id, task_id, model_id, region, prompt_version, input_tokens,"
            " output_tokens, outcome, at) values (%s, %s, %s, %s, %s, 10, 5, 'ok', %s)",
            (case, task, model, region, version, when),
        )
    return w


EXPECTED = [
    AiCallSummary("T-02", "model-b", "zz", "v1", 1, at(2), at(2)),
    AiCallSummary("T-05", "model-a", "zz", "v1", 2, at(1), at(3)),
    AiCallSummary("T-05", "model-a", "zz", "v2", 1, at(5), at(5)),
]


def test_the_summary_has_no_field_for_prompt_or_response_text() -> None:
    names = {f.name for f in fields(AiCallSummary)}
    assert names == {"task_id", "model_id", "region", "prompt_version", "calls", "first_at", "last_at"}


async def test_the_named_expert_gets_the_cases_calls_grouped(api_db: Database, world: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await ai_call_summary(conn, world["case"]) == EXPECTED


async def test_the_cases_own_job_gets_the_same_summary(worker_db: Database, world: dict[str, uuid.UUID]) -> None:
    async with worker_db.job(world["case"]) as conn:
        assert await ai_call_summary(conn, world["case"]) == EXPECTED


async def test_an_outsider_and_a_job_on_another_case_get_nothing(
    api_db: Database, worker_db: Database, world: dict[str, uuid.UUID]
) -> None:
    async with api_db.request(scope_for(world["outsider"])) as conn:
        assert await ai_call_summary(conn, world["case"]) == []
    async with worker_db.job(world["elsewhere"]) as conn:
        assert await ai_call_summary(conn, world["case"]) == []
