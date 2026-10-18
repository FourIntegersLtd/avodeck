"""What slice I needs from slice G, behind ports until G's contracts land on main
(avodeck-pending/cross-slice.md).

- `Hearings`: whether a hearing id belongs to the case (G records court orders and hearings). Until then
  only "no hearing" is valid, so packs and bundles are for the case as a whole.
- `WrittenAnswers`: the expert's served answers to written questions, for the trial pack and the offline
  copy. Until then there are none.
- `ReportChanges`: a changed position in a joint statement also creates a G `report_changes` row (rule 8:
  the served report itself never changes). Until then the change is kept on the agenda item only.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Protocol

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto


@dataclass(frozen=True)
class WrittenAnswer:
    question_no: int
    text: str


class Hearings(Protocol):
    async def belongs(self, conn: AsyncConnection, case_id: uuid.UUID, hearing_id: uuid.UUID | None) -> bool: ...


class WrittenAnswers(Protocol):
    async def served(self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> list[WrittenAnswer]: ...


class ReportChanges(Protocol):
    async def record(
        self, conn: AsyncConnection, case_id: uuid.UUID, item_id: uuid.UUID, paragraph_ids: list[uuid.UUID]
    ) -> None: ...


class NoHearings:
    async def belongs(self, conn: AsyncConnection, case_id: uuid.UUID, hearing_id: uuid.UUID | None) -> bool:
        return hearing_id is None


class NoWrittenAnswers:
    async def served(self, conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> list[WrittenAnswer]:
        return []


class NoReportChanges:
    async def record(
        self, conn: AsyncConnection, case_id: uuid.UUID, item_id: uuid.UUID, paragraph_ids: list[uuid.UUID]
    ) -> None:
        return None


@dataclass
class RecordedChanges:
    """A `ReportChanges` stand-in keeping what it was told (tests)."""

    rows: list[tuple[uuid.UUID, uuid.UUID, list[uuid.UUID]]] = field(default_factory=list)

    async def record(
        self, conn: AsyncConnection, case_id: uuid.UUID, item_id: uuid.UUID, paragraph_ids: list[uuid.UUID]
    ) -> None:
        self.rows.append((case_id, item_id, list(paragraph_ids)))
