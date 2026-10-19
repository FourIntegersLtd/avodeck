"""Slice I's ports onto slice G carry G's real shapes (migration 0700, PR #29), so wiring G in is a swap.

- A changed position becomes G `report_changes` rows: one per served paragraph, each with the served
  version, the kind, the old and new text and the reason.
- Written answers are a G `report_versions` row of type answers, reached through
  `written_question_rounds.answers_version_id`; slice I reads the served rounds and their answers.
"""

from __future__ import annotations

import dataclasses
import uuid

import pytest

from app.features.trial.ports import (
    AnsweredRound,
    NoReportChanges,
    NoWrittenAnswers,
    RecordedChanges,
    ReportChange,
    WrittenAnswer,
    answers_of,
)

pytestmark = pytest.mark.anyio


def names(cls: type) -> list[str]:
    return [f.name for f in dataclasses.fields(cls)]


def test_a_report_change_is_one_paragraph_of_a_served_version() -> None:
    assert names(ReportChange) == ["served_version_id", "paragraph_id", "kind", "old_text", "new_text", "reason"]


def test_answers_come_as_rounds_with_their_answers_version() -> None:
    assert names(AnsweredRound) == ["round_no", "answers_version_id", "answers"]
    assert names(WrittenAnswer) == ["question_no", "text"]
    first, second = uuid.uuid4(), uuid.uuid4()
    rounds = [
        AnsweredRound(1, first, (WrittenAnswer(1, "Zz one"), WrittenAnswer(2, "Zz two"))),
        AnsweredRound(2, second, (WrittenAnswer(1, "Zz again"),)),
    ]
    assert [a.text for a in answers_of(rounds)] == ["Zz one", "Zz two", "Zz again"]


async def test_the_stand_ins_record_nothing_and_find_nothing() -> None:
    case = uuid.uuid4()
    change = ReportChange(uuid.uuid4(), uuid.uuid4(), "change_of_opinion", "Zz old", "Zz new", "Zz why")
    await NoReportChanges().record(None, None, case, [change])  # type: ignore[arg-type]
    assert await NoWrittenAnswers().served(None, None, case) == []  # type: ignore[arg-type]
    recorded = RecordedChanges()
    await recorded.record(None, None, case, [change])  # type: ignore[arg-type]
    assert recorded.rows == [(case, change)]
