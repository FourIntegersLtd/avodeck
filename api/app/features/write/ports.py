"""What slice E needs from slices B and D, as Protocols with stand-ins until those slices land.

- `FactFlags` (B, screen 09): the facts the expert flagged "use in points"; they come first on screen 14.
- `QuestionLinks` (D): the citable facts linked to one question; the stand-in treats every citable fact
  as linked, so nothing is hidden before D decides the links.
- `CaseNames` (C): the names of the case's parties, so `pii.strip` takes them out before a model call
  (rule 7). The stand-in reads C's `case_parties` the way C's own jobs do, until a core contract exists.
- `StatementWords` (A): a document's words in reading order, for T-14c. The stand-in reads A's
  `ocr_words` the way A seals them, until a core contract exists; each word's reference is
  `<page_id>/<word_id>`.
- `GuidanceInForce` (D, screen 12): the guidance in force on a date, from D's `guideline_versions` (shared,
  not patient data). The stand-in returns none until D's table lands.
When B or D land, only these adapters change (cross-slice.md, E -> B and E -> D).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date
from typing import Protocol

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict

from app.core.contracts.facts import Fact
from app.core.crypto import CaseCrypto
from app.core.packs import MergedPack

PARTY_NAME = "party_name"  # slice C's encryption purpose for a party's name


def _word_purpose(page_id: uuid.UUID, word_id: str) -> str:
    return f"ocr_words/{page_id}/{word_id}"  # slice A's encryption purpose for one OCR word


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


class StatementWords(Protocol):
    async def words(self, conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> list[tuple[str, str]]:
        """(reference, text) of each of the document's words, in reading order."""
        ...


class Guidance(BaseModel):
    model_config = ConfigDict(frozen=True)

    source: str
    code: str
    title: str
    version_label: str
    in_force_from: date
    in_force_to: date | None
    url: str | None


class GuidanceInForce(Protocol):
    async def in_force(self, conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack, on: date) -> list[Guidance]:
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
    async def in_force(self, conn: AsyncConnection, case_id: uuid.UUID, pack: MergedPack, on: date) -> list[Guidance]:
        return []


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


class OcrWords:
    def __init__(self, crypto: CaseCrypto) -> None:
        self.crypto = crypto

    async def words(self, conn: AsyncConnection, case_id: uuid.UUID, document_id: uuid.UUID) -> list[tuple[str, str]]:
        cur = await conn.execute(
            "select w.page_id, w.word_id, w.text from app.ocr_words w join app.pages p on p.id = w.page_id"
            " where p.case_id = %s and p.document_id = %s order by p.bundle_page_no, w.n",
            (case_id, document_id),
        )
        return [
            (
                f"{r[0]}/{r[1]}",
                (await self.crypto.decrypt(conn, case_id, bytes(r[2]), purpose=_word_purpose(r[0], r[1]))).decode(),
            )
            for r in await cur.fetchall()
        ]
