"""What slice E needs from slices B and D, as Protocols with stand-ins until those slices land.

- `FactFlags` (B, screen 09): the facts the expert flagged "use in points"; they come first on screen 14.
- `QuestionLinks` (D): the citable facts linked to one question; the stand-in treats every citable fact
  as linked, so nothing is hidden before D decides the links.
- `CaseNames` (C): the names of the case's parties, so `pii.strip` takes them out before a model call
  (rule 7). The stand-in reads C's `case_parties` the way C's own jobs do, until a core contract exists.
When B or D land, only these adapters change (cross-slice.md, E -> B and E -> D).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Protocol

from psycopg import AsyncConnection

from app.core.contracts.facts import Fact
from app.core.crypto import CaseCrypto

PARTY_NAME = "party_name"  # slice C's encryption purpose for a party's name


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


class NoFlags:
    async def flagged_for_points(self, conn: AsyncConnection, case_id: uuid.UUID) -> set[uuid.UUID]:
        return set()


class AllLinked:
    async def linked(
        self, conn: AsyncConnection, case_id: uuid.UUID, question_id: uuid.UUID, facts: Sequence[Fact]
    ) -> list[Fact]:
        return list(facts)


class PartyNames:
    def __init__(self, crypto: CaseCrypto) -> None:
        self.crypto = crypto

    async def case_names(self, conn: AsyncConnection, case_id: uuid.UUID) -> list[str]:
        cur = await conn.execute(
            "select name from app.case_parties where case_id = %s order by created_at, id", (case_id,)
        )
        return [
            (await self.crypto.decrypt(conn, case_id, bytes(r[0]), purpose=PARTY_NAME)).decode()
            for r in await cur.fetchall()
        ]
