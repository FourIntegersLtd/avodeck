"""What slice I needs from slice G, behind ports. The `G…` adapters reach G's data through the core contracts
(slices never touch each other's tables); the `No…` and `Recorded…` stand-ins are for tests.

- `Hearings`: whether a hearing id is one of the case's scheduled hearings (G records court orders and
  hearings; `contracts/hearings.py`). "No hearing" is always valid: the pack is for the case as a whole.
- `WrittenAnswers`: the served rounds of written questions. G keeps each round's answers as a
  `report_versions` row of type answers (`written_question_rounds.answers_version_id`); slice I reads the
  round number, that version and its answers, for the trial pack and the offline copy
  (`contracts/written_answers.py`).
- `ReportChanges`: a changed position in a joint statement also creates G `report_changes` rows, one per
  served paragraph (rule 8: the served report itself never changes), sealed as G seals them
  (`contracts/report_changes.py`).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from psycopg import AsyncConnection

from app.core.contracts import report_changes, written_answers
from app.core.contracts.hearings import hearing_state
from app.core.crypto import CaseCrypto

ChangeKind = Literal["correction", "change_of_opinion"]


@dataclass(frozen=True)
class WrittenAnswer:
    question_no: int
    text: str


@dataclass(frozen=True)
class AnsweredRound:
    """One served round: its number, G's answers version (`report_versions`, type answers) and the answers."""

    round_no: int
    answers_version_id: uuid.UUID
    answers: tuple[WrittenAnswer, ...]


@dataclass(frozen=True)
class ReportChange:
    """One G `report_changes` row: a change to one paragraph of a served version."""

    served_version_id: uuid.UUID
    paragraph_id: uuid.UUID
    kind: ChangeKind
    old_text: str
    new_text: str
    reason: str


def answers_of(rounds: Iterable[AnsweredRound]) -> list[WrittenAnswer]:
    """Every answer, round by round."""
    return [answer for r in rounds for answer in r.answers]


class Hearings(Protocol):
    async def belongs(self, conn: AsyncConnection, case_id: uuid.UUID, hearing_id: uuid.UUID | None) -> bool: ...


class WrittenAnswers(Protocol):
    async def served(self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> list[AnsweredRound]: ...


class ReportChanges(Protocol):
    async def record(
        self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, changes: list[ReportChange]
    ) -> None: ...


class NoHearings:
    async def belongs(self, conn: AsyncConnection, case_id: uuid.UUID, hearing_id: uuid.UUID | None) -> bool:
        return hearing_id is None


class NoWrittenAnswers:
    async def served(self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> list[AnsweredRound]:
        return []


class NoReportChanges:
    async def record(
        self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, changes: list[ReportChange]
    ) -> None:
        return None


class GHearings:
    async def belongs(self, conn: AsyncConnection, case_id: uuid.UUID, hearing_id: uuid.UUID | None) -> bool:
        if hearing_id is None:
            return True
        state = await hearing_state(conn, case_id, hearing_id)
        return state is not None and state.status == "scheduled"


class GWrittenAnswers:
    async def served(self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> list[AnsweredRound]:
        return [
            AnsweredRound(
                r.round_no, r.answers_version_id, tuple(WrittenAnswer(a.question_no, a.text) for a in r.answers)
            )
            for r in await written_answers.served_answers(conn, crypto, case_id)
        ]


class GReportChanges:
    async def record(
        self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, changes: list[ReportChange]
    ) -> None:
        await report_changes.add_report_changes(
            conn,
            crypto,
            case_id,
            [
                report_changes.NewReportChange(
                    c.served_version_id, c.paragraph_id, c.kind, c.old_text, c.new_text, c.reason
                )
                for c in changes
            ],
        )


@dataclass
class RecordedChanges:
    """A `ReportChanges` stand-in keeping what it was told (tests)."""

    rows: list[tuple[uuid.UUID, ReportChange]] = field(default_factory=list)

    async def record(
        self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, changes: list[ReportChange]
    ) -> None:
        self.rows.extend((case_id, change) for change in changes)
