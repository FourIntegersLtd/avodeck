"""The AI kit sends each call's task key, so the answer book can replay pipeline answers (T-07b/c)."""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest

from app.core.ai.answers import AnswerBook, page_subject
from app.core.ai.kit import AiKit, AiTask
from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.packs import default_library
from tests.support.ai import cell, response
from tests.support.db import scope_for
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

TASK = AiTask(
    task_id="T-answer-test",
    prompt_version="2026-10-17.1",
    model="sonnet",
    system="Label.",
    schema={"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
)
SUBJECT = page_subject([("p1w1", "synthetic"), ("p1w2", "words")])


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
async def test_the_kit_passes_task_and_subject_and_a_recorded_retry_is_used(
    combo: Combo, tmp_path: Path, api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    case_id: uuid.UUID = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    book = AnswerBook(cell(), FixtureStore(tmp_path))
    book.record(TASK.task_id, SUBJECT, [response({"n": "not a number"}), response({"n": 2})])
    pack = default_library().combine(*combo.codes)
    async with api_db.request(scope_for(expert)) as conn:
        out = await AiKit(cell(), book).run(conn, TASK, pack=pack, text="synthetic words", case_id=case_id,
                                            subject=SUBJECT)
    assert out == {"n": 2}
    assert [(r.task_id, r.subject) for r in book.requests] == [(TASK.task_id, SUBJECT)] * 2
    outcomes = admin.execute("select outcome from app.ai_calls where case_id = %s", (case_id,)).fetchall()
    assert sorted(outcomes) == [("invalid_output",), ("ok",)]
