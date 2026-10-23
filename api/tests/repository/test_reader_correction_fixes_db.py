"""The expert's reading of misread words stays tied to the words it was made for (decision 0018, rule 2).

- When a page's words are replaced (turned, read again, typed out), every current correction on that page
  is replaced too (`superseded_at`) in the same transaction: word ids are positions on the page, so an old
  reading would sit on new words. A checked fact that cited corrected words and that the new words no
  longer show is flagged "Please check".
- Deleting a quarantined page (`app.purge_quarantined_page`) deletes the page's corrections.
- Where a correction lands: the run's real first word carries the expert's reading and the rest of the
  run reads as nothing. When only some of the page's words are given (a fact's cited words) and the run's
  first word is not among them, the first given word of the run carries it. So the reader and
  `core/contracts/document_words.cited_words` agree on what a fact's words say.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.contracts import document_words
from app.core.contracts.document_words import correction_purpose
from app.core.contracts.pipeline import extract_page_text, process_pages
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.features.reader import repo
from tests.fixtures.stub_facts import load_stub_facts
from tests.repository.test_pipeline_quarantine import OTHER_PAGE, page_id
from tests.repository.test_pipeline_quarantine_purge import confirmed_on, purge
from tests.repository.test_pipeline_quarantine_purge import h as quarantined  # noqa: F401 (fixture)
from tests.repository.test_pipeline_reread import (
    add_fact,
    page_ids,
    rechecks,
    sealed_typing,
    storage,  # noqa: F401 (fixture)
    words_for,
)
from tests.repository.test_pipeline_reread import h as reread  # noqa: F401 (fixture)
from tests.repository.test_pipeline_support import Harness
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


async def add_correction(h: Harness, page: uuid.UUID, word_ids: list[str], text: str) -> uuid.UUID:
    """A current correction, sealed with the case key as the reader seals it."""
    cid = uuid.uuid4()
    async with h.api_db.request(scope_for(h.expert)) as conn:
        sealed = await h.crypto.encrypt(conn, h.case_id, text.encode(), purpose=correction_purpose(page, cid))
    h.admin.execute(
        "insert into app.word_corrections (id, page_id, word_ids, text, person_id) values (%s, %s, %s, %s, %s)",
        (cid, page, word_ids, sealed, h.expert),
    )
    return cid


def superseded(h: Harness, cid: uuid.UUID) -> bool:
    row = h.admin.execute("select superseded_at is not null from app.word_corrections where id = %s", (cid,)).fetchone()
    assert row is not None
    return bool(row[0])


# 1. Words replaced ---------------------------------------------------------------------------------


@pytest.mark.parametrize("how", ["turned", "typed_out"])
async def test_replacing_a_pages_words_replaces_its_corrections_and_flags_facts_relying_on_them(
    reread: Harness,  # noqa: F811
    how: str,
) -> None:
    h = reread
    first, second = page_ids(h)
    cited = await words_for(h, first, "chest clear")
    relying = add_fact(h, first, "chest infection", cited, "verified")
    here = await add_correction(h, first, cited, "chest infection")
    elsewhere = await add_correction(h, second, await words_for(h, second, "Second page"), "Second sheet")

    if how == "typed_out":
        h.admin.execute(
            "update app.pages set unreadable = true, transcribed_by_expert_text = %s where id = %s",
            (await sealed_typing(h, first, "Seen today chest clear"), first),
        )
    async with h.api_db.request(scope_for(h.expert)) as conn:
        if how == "turned":
            h.admin.execute("update app.pages set rotation = 90 where id = %s", (first,))
            await process_pages(conn, h.case_id, [first])
        else:
            await extract_page_text(conn, h.case_id, first)
    assert set((await h.drain()).values()) == {"done"}

    assert superseded(h, here), "the old reading still sits on the page's new words"
    assert not superseded(h, elsewhere), "another page's reading was replaced"
    assert (relying, None) in rechecks(h), "a fact the new words no longer show was not flagged"
    cites = h.admin.execute(
        "select exists (select 1 from app.word_corrections c where c.page_id = f.page_id"
        " and c.superseded_at is null and c.word_ids && f.word_ids) from app.facts f where f.id = %s",
        (relying,),
    ).fetchone()
    assert cites == (False,), "the fact still says it cites the expert's reading"


# 2. Quarantine purge -------------------------------------------------------------------------------


async def test_deleting_a_quarantined_page_deletes_its_corrections(quarantined: Harness) -> None:  # noqa: F811
    h = quarantined
    gone = await add_correction(h, page_id(h, OTHER_PAGE), ["p1w1"], "another reading")
    kept = await add_correction(h, page_id(h, 1), ["p1w1"], "kept reading")
    await purge(h, confirmed_on(h) + dt.timedelta(days=h.days))  # type: ignore[attr-defined]
    left = {r[0] for r in h.admin.execute("select id from app.word_corrections where id = any (%s)", ([gone, kept],))}
    assert left == {kept}


# 5. Where the expert's reading lands ---------------------------------------------------------------


def test_the_reading_lands_on_the_runs_real_first_word() -> None:
    words = {"w1": "3097", "w2": "texturas", "w3": "booster", "w4": "giver"}
    applied = document_words.apply_corrections(words, [(["w3", "w2"], "tetanus booster")])
    assert applied == {"w1": "3097", "w2": "tetanus booster", "w3": "", "w4": "giver"}


def test_given_part_of_a_run_the_reading_lands_on_its_first_given_word() -> None:
    cited = {"w3": "booster", "w4": "giver", "w5": "today"}
    applied = document_words.apply_corrections(cited, [(["w2", "w3", "w4"], "tetanus booster given")])
    assert applied == {"w3": "tetanus booster given", "w4": "", "w5": "today"}


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def page118(seed: Seed, aconn: psycopg.AsyncConnection) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(
        seed.org(kind="personal", members={expert: "owner"}), expert, status="active", packs=LAUNCH.case_row()
    )
    loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO, verified=True)
    cur = await aconn.execute(
        "select id, word_ids from app.facts where page_id = %s and value is not null and cardinality(word_ids) >= 3"
        " order by created_at, id limit 1",
        (loaded[118].page_id,),
    )
    row = await cur.fetchone()
    assert row is not None
    return {"case": case, "expert": expert, "page_id": loaded[118].page_id, "fact": row[0], "words": list(row[1])}


async def test_the_reader_and_cited_words_agree_on_what_a_fact_cites(
    api_db: Database, aconn: psycopg.AsyncConnection, page118: dict[str, Any]
) -> None:
    w = page118
    a1, a2, a3 = w["words"][:3]
    async with api_db.request(scope_for(w["expert"])) as conn:
        await repo.save_correction(conn, CRYPTO, w["case"], w["page_id"], [a1, a2], "tetanus booster", w["expert"])
    cur = await aconn.execute(
        "insert into app.facts (case_id, page_id, kind, value, word_ids) select case_id, page_id, kind, 'x', %s"
        " from app.facts where id = %s returning id",
        ([a2, a3], w["fact"]),
    )
    row = await cur.fetchone()
    assert row is not None
    partial = row[0]

    cited = await document_words.cited_words(aconn, CRYPTO, w["case"], [partial])
    async with api_db.request(scope_for(w["expert"])) as conn:
        reader = await repo.fact_reading(conn, CRYPTO, w["case"], w["page_id"], [a2, a3])
    assert [(k, t) for k, t in reader.items() if t] == cited[partial]
    assert cited[partial][0] == (a2, "tetanus booster")
