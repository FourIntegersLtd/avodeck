"""Read contracts so slices D and E stop reading other slices' tables directly.

`document_words` opens slice A's sealed `ocr_words` (a document's words in reading order, or the words
each fact cites); `case_names` opens slice C's sealed party names for `pii.strip` (rule 7). Both run
on the caller's connection, so row-level security applies: an outsider gets nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.contracts import case_names, document_words
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from tests.support.contracts import add_fact
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, outsider = seed.person(), seed.person()
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    doc, other = seed.document(case), seed.document(case)
    return {
        "expert": expert,
        "outsider": outsider,
        "case": case,
        "doc": doc,
        "p1": seed.page(case, doc, 1),
        "p2": seed.page(case, doc, 2),
        "p3": seed.page(case, other, 3),
    }


async def add_words(aconn: psycopg.AsyncConnection, case: uuid.UUID, page: uuid.UUID, words: list[str]) -> None:
    for n, text in enumerate(words, start=1):
        word_id = f"w{n}"
        sealed = await CRYPTO.encrypt(
            aconn, case, text.encode(), purpose=document_words.word_purpose(page, word_id)
        )
        await aconn.execute(
            "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence)"
            " values (%s, %s, %s, %s, %s, '{0,0,1,0,1,1,0,1}', 0.99)",
            (page, case, n, word_id, sealed),
        )


def test_the_word_seal_is_the_one_slice_a_uses() -> None:
    from app.features.pipeline import paths

    page = uuid.uuid4()
    assert document_words.word_purpose(page, "w7") == paths.word_purpose(page, "w7")


async def test_a_documents_words_open_in_reading_order(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case = world["case"]
    await add_words(aconn, case, world["p2"], ["review", "soon"])
    await add_words(aconn, case, world["p1"], ["chest", "clear"])
    await add_words(aconn, case, world["p3"], ["elsewhere"])
    async with api_db.request(scope_for(world["expert"])) as conn:
        found = await document_words.document_words(conn, CRYPTO, case, world["doc"])
    p1, p2 = world["p1"], world["p2"]
    assert found == [
        (f"{p1}/w1", "chest"),
        (f"{p1}/w2", "clear"),
        (f"{p2}/w1", "review"),
        (f"{p2}/w2", "soon"),
    ]
    async with api_db.request(scope_for(world["outsider"])) as conn:
        assert await document_words.document_words(conn, CRYPTO, case, world["doc"]) == []


async def test_the_words_each_fact_cites_open_in_reading_order(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case = world["case"]
    await add_words(aconn, case, world["p1"], ["chest", "clear", "today"])
    fact = add_fact(admin, case, world["p1"], value="chest today", word_ids=["w3", "w1"])
    async with api_db.request(scope_for(world["expert"])) as conn:
        found = await document_words.cited_words(conn, CRYPTO, case, [fact])
    assert found == {fact: [("w1", "chest"), ("w3", "today")]}


async def test_party_names_open_for_identifier_stripping(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case = world["case"]
    for position, name in enumerate(["Zebedee Quillfeather", "Al Bo"]):
        sealed = await CRYPTO.encrypt(aconn, case, name.encode(), purpose=case_names.PARTY_NAME_PURPOSE)
        await aconn.execute(
            "insert into app.case_parties (case_id, role_key, name, position) values (%s, 'party_a', %s, %s)",
            (case, sealed, position),
        )
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await case_names.party_names(conn, CRYPTO, case) == ["Zebedee Quillfeather", "Al Bo"]
        assert await case_names.party_names(conn, CRYPTO, case, with_parts=True) == [
            "Zebedee Quillfeather",
            "Zebedee",
            "Quillfeather",
            "Al Bo",
        ], "parts of three letters or more, as slice D's jobs strip them"
    async with api_db.request(scope_for(world["outsider"])) as conn:
        assert await case_names.party_names(conn, CRYPTO, case) == []
