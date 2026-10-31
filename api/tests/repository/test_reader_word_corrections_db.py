"""The expert's reading of misread words (`word_corrections`, migration 0209, decision 0018).

- A correction belongs to a page (no `case_id` column): row-level security finds the case through
  `pages`. Case viewers read it; only the named expert adds one, as themselves; nobody deletes one or
  changes its words or text; the only change is marking it replaced (`superseded_at`, once).
- The worker reads only its own job's case.
- Deleting a case (`app.purge_case`) leaves no corrections: they go with their pages.
- Through `core/contracts/document_words`, the words a fact cites read as the expert's reading
  (the computer's reading stays in `ocr_words`); `citable_facts()` marks facts citing a correction.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.contracts import document_words
from app.core.contracts.facts import citable_facts
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.features.reader import repo
from app.features.reader.log import ReaderPageReadLog
from tests.fixtures.stub_facts import load_stub_facts
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
READING = "tetanus booster given third dose"


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def world(seed: Seed, aconn: psycopg.AsyncConnection) -> dict[str, Any]:
    expert, assistant, stranger = seed.person(), seed.person(), seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="active", packs=LAUNCH.case_row())
    seed.case_member(case, assistant, "assistant", added_by=expert)
    loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO, verified=True)
    page = loaded[118].page_id
    cur = await aconn.execute(
        "select id, word_ids, value from app.facts where page_id = %s and value is not null order by created_at, id",
        (page,),
    )
    rows = await cur.fetchall()
    return {
        "case": case,
        "org": org,
        "expert": expert,
        "assistant": assistant,
        "stranger": stranger,
        "page_id": page,
        "facts": [{"id": r[0], "word_ids": list(r[1]), "value": r[2]} for r in rows],
    }


async def correct(api_db: Database, w: dict[str, Any], word_ids: list[str], text: str = READING) -> uuid.UUID:
    async with api_db.request(scope_for(w["expert"])) as conn:
        return await repo.save_correction(conn, CRYPTO, w["case"], w["page_id"], word_ids, text, w["expert"])


async def test_viewers_read_corrections_and_strangers_see_none(api_db: Database, world: dict[str, Any]) -> None:
    await correct(api_db, world, world["facts"][0]["word_ids"])
    for person, seen in ((world["expert"], 1), (world["assistant"], 1), (world["stranger"], 0)):
        async with api_db.request(scope_for(person)) as conn:
            cur = await conn.execute(
                "select count(*) from app.word_corrections where page_id = %s", (world["page_id"],)
            )
            row = await cur.fetchone()
        assert row == (seen,), person


async def test_only_the_named_expert_adds_a_correction_as_themselves(api_db: Database, world: dict[str, Any]) -> None:
    sql = "insert into app.word_corrections (page_id, word_ids, text, person_id) values (%s, %s, %s, %s)"
    for person, as_person in (
        (world["assistant"], world["assistant"]),
        (world["stranger"], world["stranger"]),
        (world["expert"], world["assistant"]),
    ):
        async with api_db.request(scope_for(person)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(sql, (world["page_id"], ["p118w1"], b"sealed", as_person))


async def test_corrections_are_never_deleted_or_rewritten_and_replaced_only_once(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    cid = await correct(api_db, world, world["facts"][0]["word_ids"])
    for sql in (
        "delete from app.word_corrections where id = %s",
        "update app.word_corrections set text = 'x'::bytea where id = %s",
        "update app.word_corrections set word_ids = '{p118w99}' where id = %s",
    ):
        async with api_db.request(scope_for(world["expert"])) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute(sql, (cid,))
    async with api_db.request(scope_for(world["expert"])) as conn:
        await conn.execute("update app.word_corrections set superseded_at = now() where id = %s", (cid,))
    async with api_db.request(scope_for(world["expert"])) as conn:
        cur = await conn.execute("update app.word_corrections set superseded_at = null where id = %s", (cid,))
        assert cur.rowcount == 0, "a replaced reading is not brought back"
    row = admin.execute("select superseded_at from app.word_corrections where id = %s", (cid,)).fetchone()
    assert row is not None and row[0] is not None


async def test_the_worker_reads_only_its_own_jobs_case(
    api_db: Database, worker_dsn: str, seed: Seed, world: dict[str, Any]
) -> None:
    await correct(api_db, world, world["facts"][0]["word_ids"])
    other = seed.case(world["org"], world["expert"], status="active")
    for job_case, seen in ((world["case"], 1), (other, 0)):
        with psycopg.connect(worker_dsn) as conn:
            conn.execute("select set_config('app.job_case_id', %s, false)", (str(job_case),))
            row = conn.execute(
                "select count(*) from app.word_corrections where page_id = %s", (world["page_id"],)
            ).fetchone()
        assert row == (seen,)


async def test_deleting_the_case_leaves_no_corrections(
    api_db: Database, admin: psycopg.Connection, worker_dsn: str, world: dict[str, Any]
) -> None:
    await correct(api_db, world, world["facts"][0]["word_ids"])
    admin.execute("update app.cases set status = 'served' where id = %s", (world["case"],))
    admin.execute(
        "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%s, %s, 'zz-key')"
        " on conflict (case_id) do nothing",
        (world["case"], b"wrapped"),
    )
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(world["case"]),))
        conn.execute("select app.purge_case(%s)", (world["case"],))
    row = admin.execute("select count(*) from app.word_corrections where page_id = %s", (world["page_id"],)).fetchone()
    assert row == (0,)


async def test_cited_words_read_as_the_experts_reading(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    fact = world["facts"][0]
    await correct(api_db, world, fact["word_ids"])
    cited = await document_words.cited_words(aconn, CRYPTO, world["case"], [fact["id"]])
    assert cited[fact["id"]] == [(fact["word_ids"][0], READING)]
    doc = await aconn.execute("select document_id from app.pages where id = %s", (world["page_id"],))
    row = await doc.fetchone()
    assert row is not None
    words = dict(await document_words.document_words(aconn, CRYPTO, world["case"], row[0]))
    page = world["page_id"]
    assert words[f"{page}/{fact['word_ids'][0]}"] == READING
    assert all(f"{page}/{w}" not in words for w in fact["word_ids"][1:])
    untouched = world["facts"][1]
    assert cited.keys() == {fact["id"]}
    other = await document_words.cited_words(aconn, CRYPTO, world["case"], [untouched["id"]])
    assert " ".join(t for _, t in other[untouched["id"]]) == untouched["value"]


async def test_apply_corrections_keeps_every_word_and_puts_the_reading_on_the_first() -> None:
    words = {"w1": "3097", "w2": "texturas", "w3": "booster", "w4": "giver"}
    applied = document_words.apply_corrections(words, [(["w2", "w3"], "tetanus booster")])
    assert list(applied) == ["w1", "w2", "w3", "w4"]
    assert applied == {"w1": "3097", "w2": "tetanus booster", "w3": "", "w4": "giver"}


async def test_citable_facts_mark_the_ones_citing_a_correction(
    api_db: Database, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    fact = world["facts"][0]
    async with api_db.request(scope_for(world["expert"])) as conn:
        await repo.record_read(conn, world["case"], world["page_id"], world["expert"], 20)
    await correct(api_db, world, fact["word_ids"])
    citable = {f.id: f for f in await citable_facts(aconn, world["case"], ReaderPageReadLog())}
    assert citable[fact["id"]].cites_correction is True
    assert citable[world["facts"][1]["id"]].cites_correction is False
