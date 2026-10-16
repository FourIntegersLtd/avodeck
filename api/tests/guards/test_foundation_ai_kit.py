"""The AI kit (architecture 6.2, 8.3): the only way any slice calls a model.

Every call strips identifiers first (rule 7), uses the cell's in-region model for the task's role
(rule 5), sends the pack's prompt context as data, asks for structured output at temperature 0 and
accepts only output matching the task's schema (one retry), and writes an `ai_calls` row holding
metadata only, never content (rule 6, architecture 9 "AI").
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.ai.kit import AiKit, AiTask, InvalidOutput
from app.core.ai.cell import ModelNotAllowed
from app.core.db import Database
from app.core.packs import MergedPack, default_library
from tests.support.ai import CELL_ENV, NAME, ScriptedBedrock, cell, mod11_number, response, spaced
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

TASK = AiTask(
    task_id="T-test",
    prompt_version="2026-10-16.1",
    model="sonnet",
    system="Label each line with one of the allowed labels.",
    schema={
        "type": "object",
        "properties": {"labels": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}},
        "required": ["labels"],
        "additionalProperties": False,
    },
)
NUMBER = mod11_number("943476591")
TEXT = f"{NAME} {spaced(NUMBER)} seen 14/03/2020 with a cough."
CONTENT_WORDS = {"prompt", "response", "text", "input", "output", "content", "body", "message"}


@pytest.fixture
def pack() -> MergedPack:
    return default_library().combine(*LAUNCH.codes)


@pytest.fixture
def case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    return expert, seed.case(org, expert, packs=LAUNCH.case_row())


async def test_a_call_strips_identifiers_and_records_only_metadata(
    api_db: Database, admin: psycopg.Connection, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    model = ScriptedBedrock([response({"labels": ["a"]}, 120, 8)])
    async with api_db.request(scope_for(expert)) as conn:
        out = await AiKit(cell(), model).run(conn, TASK, pack=pack, case_id=case_id, text=TEXT, names=[NAME])
    assert out == {"labels": ["a"]}

    [sent] = model.requests
    for leaked in (NAME, "Pemberton", NUMBER, spaced(NUMBER)):
        assert leaked not in sent.text
    assert "14/03/2020" in sent.text
    assert sent.model_id == CELL_ENV["BEDROCK_SONNET_MODEL_ID"]
    assert sent.temperature == 0
    assert sent.schema == TASK.schema
    assert sent.system == TASK.system
    assert sent.context == pack.prompt_context()

    rows = admin.execute(
        "select task_id, model_id, region, prompt_version, input_tokens, output_tokens, outcome, person_id"
        " from app.ai_calls where case_id = %s",
        (case_id,),
    ).fetchall()
    assert rows == [("T-test", sent.model_id, "eu-west-2", "2026-10-16.1", 120, 8, "ok", expert)]


def test_ai_calls_has_no_column_that_could_hold_content(admin: psycopg.Connection) -> None:
    columns = {
        r[0]
        for r in admin.execute(
            "select column_name from information_schema.columns where table_schema = 'app' and table_name = 'ai_calls'"
        ).fetchall()
    }
    assert {"task_id", "model_id", "region", "prompt_version", "input_tokens", "output_tokens"} <= columns
    assert not columns & CONTENT_WORDS


async def test_ai_calls_are_append_only_and_private_to_the_case(
    api_db: Database, seed: Seed, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    outsider = seed.person()
    async with api_db.request(scope_for(expert)) as conn:
        await AiKit(cell(), ScriptedBedrock([response({"labels": []})])).run(
            conn, TASK, pack=pack, case_id=case_id, text="nothing to strip"
        )
    async with api_db.request(scope_for(outsider)) as conn:
        cur = await conn.execute("select count(*) from app.ai_calls where case_id = %s", (case_id,))
        assert await cur.fetchone() == (0,)
    for statement in ("update app.ai_calls set input_tokens = 0", "delete from app.ai_calls"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with api_db.request(scope_for(expert)) as conn:
                await conn.execute(statement + " where case_id = %s", (case_id,))


async def test_an_outsider_cannot_spend_ai_on_a_case(
    api_db: Database, seed: Seed, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _, case_id = case
    outsider = seed.person()
    model = ScriptedBedrock([response({"labels": []})])
    with pytest.raises(psycopg.Error):
        async with api_db.request(scope_for(outsider)) as conn:
            await AiKit(cell(), model).run(conn, TASK, pack=pack, case_id=case_id, text="x")
    assert model.requests == []


async def test_output_outside_the_schema_is_retried_once_then_refused(
    api_db: Database, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    model = ScriptedBedrock([response({"labels": ["free prose"]}), response({"opinion": "x"})])
    with pytest.raises(InvalidOutput):
        async with api_db.request(scope_for(expert)) as conn:
            await AiKit(cell(), model).run(conn, TASK, pack=pack, case_id=case_id, text="x")
    assert len(model.requests) == 2

    model = ScriptedBedrock([response({"labels": ["c"]}), response({"labels": ["b"]})])
    async with api_db.request(scope_for(expert)) as conn:
        assert await AiKit(cell(), model).run(conn, TASK, pack=pack, case_id=case_id, text="x") == {"labels": ["b"]}


async def test_the_kit_refuses_a_role_without_an_in_region_model(
    api_db: Database, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    model = ScriptedBedrock([response({"labels": []})])
    task = AiTask(TASK.task_id, TASK.prompt_version, "haiku", TASK.system, TASK.schema)
    with pytest.raises(ModelNotAllowed):
        async with api_db.request(scope_for(expert)) as conn:
            await AiKit(cell(), model).run(conn, task, pack=pack, case_id=case_id, text="x")
    assert model.requests == []


async def test_the_kit_refuses_a_client_pointed_outside_the_cell(
    api_db: Database, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from app.core.ai.cell import EndpointNotAllowed

    expert, case_id = case
    model = ScriptedBedrock([response({"labels": []})], endpoint="https://bedrock-runtime.us-east-1.amazonaws.com")
    with pytest.raises(EndpointNotAllowed):
        async with api_db.request(scope_for(expert)) as conn:
            await AiKit(cell(), model).run(conn, TASK, pack=pack, case_id=case_id, text="x")
    assert model.requests == []
