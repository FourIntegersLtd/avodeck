"""What slice E needs from slices A, B, C and D, as Protocols, with the cell's adapters and test stand-ins.

- `FactFlags` (B, screen 09): the facts the expert flagged "use in points"; they come first on screen 14.
  The cell reads `core/contracts/fact_flags`.
- `QuestionLinks` (D): the citable facts linked to one question. The cell keeps only the facts the expert
  confirmed for the question (`core/contracts/fact_links`); `AllLinked` is the stand-in tests use.
- `CaseNames` (C): the names of the case's parties, so `pii.strip` takes them out before a model call
  (rule 7), from `core/contracts/case_names`.
- `BirthDates`: the subject's dates of birth, taken out the same way. No slice stores one yet, so the
  cell has none (the pipeline does the same); tests pass their own.
- `StatementWords` (A): a document's words in reading order, for T-14c, from
  `core/contracts/document_words`; each word's reference is `<page_id>/<word_id>`.
- `GuidanceInForce` (D, screen 12): the guidance in force on a date. The cell reads the overlay pack's
  guideline list (shared, not patient data).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Protocol

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from app.core.contracts.case_names import party_names
from app.core.contracts.document_words import document_words
from app.core.contracts.fact_flags import flagged_fact_ids
from app.core.contracts.fact_links import confirmed_links
from app.core.contracts.facts import Fact
from app.core.crypto import CaseCrypto
from app.core.packs import MergedPack


class FactFlags(Protocol):
    async def flagged_for_points(self, conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
        """The ids of the case's facts the expert flagged for points."""
        ...


class QuestionLinks(Protocol):
    async def linked(
        self, conn: AsyncConnection, case_id: uuid.UUID, question_id: uuid.UUID, facts: Sequence[Fact]
    ) -> list[Fact]:
        """Those of `facts` linked to the question."""
        ...


class CaseNames(Protocol):
    async def case_names(self, conn: AsyncConnection, case_id: uuid.UUID) -> list[str]:
        """The names of the case's parties."""
        ...


class BirthDates(Protocol):
    async def dates_of_birth(self, conn: AsyncConnection, case_id: uuid.UUID) -> list[date]:
        """The subject's dates of birth."""
        ...


class StatementWords(Protocol):
    async def words(self, conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> list[tuple[str, str]]:
        """(reference, text) of each of the document's words, in reading order."""
        ...


class WorkspaceGuidance(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    code: str
    title: str
    version_label: str
    in_force_from: date
    in_force_to: date | None
    url: str | None


Guidance = WorkspaceGuidance  # the name slice D's consultation Guidance already takes in OpenAPI


class GuidanceInForce(Protocol):
    async def in_force(
        self, conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack, on: date
    ) -> list[WorkspaceGuidance]:
        """The guidance for the case's jurisdiction and specialty in force on `on`."""
        ...


class NoFlags:
    async def flagged_for_points(self, conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
        return set()


class AllLinked:
    async def linked(
        self, conn: AsyncConnection, case_id: uuid.UUID, question_id: uuid.UUID, facts: Sequence[Fact]
    ) -> list[Fact]:
        return list(facts)


class NoGuidance:
    async def in_force(
        self, conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack, on: date
    ) -> list[WorkspaceGuidance]:
        return []


class NoBirthDates:
    async def dates_of_birth(self, conn: AsyncConnection, case_id: uuid.UUID) -> list[date]:
        return []


class PointFlags:
    async def flagged_for_points(self, conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
        return await flagged_fact_ids(conn, case_id)


class ConfirmedLinks:
    async def linked(
        self, conn: AsyncConnection, case_id: uuid.UUID, question_id: uuid.UUID, facts: Sequence[Fact]
    ) -> list[Fact]:
        confirmed = (await confirmed_links(conn, case_id, question_id)).get(question_id, set())
        return [f for f in facts if f.id in confirmed]


def _day(value: object) -> date | None:
    return None if value in (None, "") else date.fromisoformat(str(value))


class PackGuidance:
    async def in_force(
        self, conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack, on: date
    ) -> list[WorkspaceGuidance]:
        out = []
        for row in pack.guidelines():
            start, end = _day(row.get("in_force_from")), _day(row.get("in_force_to"))
            if start is None or start > on or (end is not None and on >= end):
                continue
            out.append(
                WorkspaceGuidance(
                    source=str(row["source"]),
                    code=str(row["code"]),
                    title=str(row["title"]),
                    version_label=str(row["version_label"]),
                    in_force_from=start,
                    in_force_to=end,
                    url=None if row.get("url") is None else str(row["url"]),
                )
            )
        return out


class PartyNames:
    def __init__(self, crypto: CaseCrypto) -> None:
        self.crypto = crypto

    async def case_names(self, conn: AsyncConnection, case_id: uuid.UUID) -> list[str]:
        return await party_names(conn, self.crypto, case_id)


class OcrWords:
    def __init__(self, crypto: CaseCrypto) -> None:
        self.crypto = crypto

    async def words(self, conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> list[tuple[str, str]]:
        return await document_words(conn, self.crypto, case_id, document_id)
