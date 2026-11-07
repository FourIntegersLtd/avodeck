"""An answer that used the whole output cap is cut off, not wrong: asking the same thing with the same cap
only fails the same way, so the kit gives it up at once as `OutputTruncated` and the caller makes it smaller
(T-07b). A wrong answer of ordinary size is still asked again once."""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest

from app.core.ai.kit import AiKit, AiTask, InvalidOutput, OutputTruncated
from app.core.db import Database
from app.core.packs import MergedPack, default_library
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CAP = 500
TASK = AiTask(
    task_id="T-test",
    prompt_version="2026-10-16.1",
    model="sonnet",
    system="Label each line.",
    schema={
        "type": "object",
        "properties": {"labels": {"type": "array", "items": {"type": "string"}}},
        "required": ["labels"],
        "additionalProperties": False,
    },
    max_tokens=CAP,
)


@pytest.fixture
def pack() -> MergedPack:
    return default_library().combine(*LAUNCH.codes)


@pytest.fixture
def case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    return expert, seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())


async def test_an_answer_that_used_the_whole_cap_is_not_asked_again_with_the_same_cap(
    api_db: Database, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    model = ScriptedBedrock([response({}, 100, CAP), response({"labels": ["a"]}, 100, 10)])
    with pytest.raises(OutputTruncated):
        async with api_db.request(scope_for(expert)) as conn:
            await AiKit(cell(), model).run(conn, TASK, pack=pack, case_id=case_id, text="x")
    assert len(model.requests) == 1
    assert issubclass(OutputTruncated, InvalidOutput)


async def test_a_wrong_answer_of_ordinary_size_is_still_asked_again_once(
    api_db: Database, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    model = ScriptedBedrock([response({}, 100, CAP - 1), response({"labels": ["a"]}, 100, 10)])
    async with api_db.request(scope_for(expert)) as conn:
        out = await AiKit(cell(), model).run(conn, TASK, pack=pack, case_id=case_id, text="x")
    assert out == {"labels": ["a"]}
    assert len(model.requests) == 2


BIG = replace(
    TASK,
    schema={
        "type": "object",
        "properties": {"kind": {"enum": ["a", "b"]}},
        "required": ["kind"],
        "additionalProperties": False,
    },
    max_tokens=32000,
)


async def test_an_answer_cut_off_just_short_of_the_cap_with_a_broken_tail_is_not_asked_again(
    api_db: Database, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    """Seen in production: 31934 of 32000 tokens and an `AdditionalProperties` error, 66 short of the cap."""
    expert, case_id = case
    model = ScriptedBedrock([response({"kind": "a", "x": 1}, 100, 31934), response({"kind": "a"}, 100, 10)])
    with pytest.raises(OutputTruncated):
        async with api_db.request(scope_for(expert)) as conn:
            await AiKit(cell(), model).run(conn, BIG, pack=pack, case_id=case_id, text="x")
    assert len(model.requests) == 1


async def test_a_clean_off_list_label_at_ordinary_size_is_still_asked_again_once(
    api_db: Database, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    model = ScriptedBedrock([response({"kind": "z"}, 100, 10525), response({"kind": "a"}, 100, 10)])
    async with api_db.request(scope_for(expert)) as conn:
        out = await AiKit(cell(), model).run(conn, BIG, pack=pack, case_id=case_id, text="x")
    assert out == {"kind": "a"}
    assert len(model.requests) == 2
