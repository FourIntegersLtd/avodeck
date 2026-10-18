"""What slice E needs from slices B and D, as Protocols with stand-ins until those slices land.

- `FactFlags` (B, screen 09): the facts the expert flagged "use in points"; they come first on screen 14.
- `QuestionLinks` (D): the citable facts linked to one question; the stand-in treats every citable fact
  as linked, so nothing is hidden before D decides the links.
When B or D land, only these adapters change (cross-slice.md, E -> B and E -> D).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol

from psycopg import AsyncConnection

from app.core.contracts.facts import Fact


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


class NoFlags:
    async def flagged_for_points(self, conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
        return set()


class AllLinked:
    async def linked(
        self, conn: AsyncConnection, case_id: uuid.UUID, question_id: uuid.UUID, facts: Sequence[Fact]
    ) -> list[Fact]:
        return list(facts)
