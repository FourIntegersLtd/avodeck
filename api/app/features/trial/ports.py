"""What slice I needs from slice G, behind ports until G's contracts land on main
(avodeck-pending/cross-slice.md). The shapes are G's own (migration 0700), so the real adapters are a swap.

- `Hearings`: whether a hearing id belongs to the case (G records court orders and hearings). Until then
  only "no hearing" is valid, so packs and bundles are for the case as a whole.
- `WrittenAnswers`: the served rounds of written questions. G keeps each round's answers as a
  `report_versions` row of type answers (`written_question_rounds.answers_version_id`); slice I reads the
  round number, that version and its answers, for the trial pack and the offline copy. Until then none.
- `ReportChanges`: a changed position in a joint statement also creates G `report_changes` rows, one per
  served paragraph (rule 8: the served report itself never changes). G seals the texts. Until then the
  change is kept on the agenda item only.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Literal, Protocol

from psycopg import AsyncConnection

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


@dataclass
class RecordedChanges:
    """A `ReportChanges` stand-in keeping what it was told (tests)."""

    rows: list[tuple[uuid.UUID, ReportChange]] = field(default_factory=list)

    async def record(
        self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, changes: list[ReportChange]
    ) -> None:
        self.rows.extend((case_id, change) for change in changes)
