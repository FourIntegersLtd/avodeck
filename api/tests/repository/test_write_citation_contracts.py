"""Slice E's out-contracts for slice F (architecture 8.3): `latest_opinions` and `citation_problems`.

F builds the report from the latest saved opinion on each question and, in its final check, re-checks
every saved citation, including the ones the expert chose to keep after their fact changed (write keeps
those as they were, architecture 10.4). A citation is a problem when its fact is gone (`missing`), no
longer citable (rejected, unchecked, flagged "Please check", or on a page the named expert has not read:
`not_citable`), or differs from what was cited (page, words or version: `changed_since_cited`).
Both run on the caller's connection, so row-level security applies: an outsider gets nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.core.contracts import citations, opinions
from app.core.contracts.fact_flags import RECHECK
from app.core.contracts.reading import PageReads
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from tests.support.contracts import add_fact, add_question
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, outsider = seed.person(), seed.person()
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    doc = seed.document(case)
    read, unread = seed.page(case, doc, 1), seed.page(case, doc, 2)
    admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds, counts_for_citing)"
        " values (%s, %s, %s, 30, true)",
        (case, read, expert),
    )
    return {
        "expert": expert,
        "outsider": outsider,
        "case": case,
        "read": read,
        "unread": unread,
        "q1": add_question(admin, case, number=1, status="confirmed"),
        "q2": add_question(admin, case, number=2, status="confirmed"),
    }


def cite(fact: uuid.UUID, page: uuid.UUID, quote: str = "9 ug/L", version: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"fact_id": str(fact), "page_id": str(page), "quote_verbatim": quote}
    if version is not None:
        out["fact_version"] = version
    return out


async def save_opinion(
    aconn: psycopg.AsyncConnection,
    case: uuid.UUID,
    question: uuid.UUID,
    body: str,
    refs: list[dict[str, Any]],
    test_text: str | None = None,
) -> uuid.UUID:
    """Save (or save again) the opinion on a question as slice E does: sealed text, citations as JSON."""
    sealed = await CRYPTO.encrypt(aconn, case, body.encode(), purpose=opinions.BODY_PURPOSE)
    sealed_test = (
        None
        if test_text is None
        else await CRYPTO.encrypt(aconn, case, test_text.encode(), purpose=opinions.TEST_TEXT_PURPOSE)
    )
    cur = await aconn.execute(
        "insert into app.opinions (case_id, question_id, body, citation_refs, view, test_text)"
        " values (%s, %s, %s, %s, 'zz_view', %s)"
        " on conflict (question_id) do update set body = excluded.body, citation_refs = excluded.citation_refs,"
        " test_text = excluded.test_text returning id",
        (case, question, sealed, Jsonb(refs), sealed_test),
    )
    row = await cur.fetchone()
    assert row is not None
    opinion_id: uuid.UUID = row[0]
    return opinion_id


def test_write_shares_the_contracts_citation_and_seals() -> None:
    from app.features.write import citations as write_citations
    from app.features.write import lint_store, routes_opinion

    assert write_citations.Citation is citations.Citation, "one citation shape for E and F"
    assert write_citations.markers is citations.markers
    assert lint_store.BODY == opinions.BODY_PURPOSE
    assert routes_opinion.TEST_TEXT == opinions.TEST_TEXT_PURPOSE


async def test_every_saved_citation_is_checked_again_kept_ones_included(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case, read, unread = world["case"], world["read"], world["unread"]
    ok = add_fact(admin, case, read, status="verified", value="ferritin 9 ug/L")
    rejected = add_fact(admin, case, read, status="rejected")
    unchecked = add_fact(admin, case, read, status="suggested")
    recheck = add_fact(admin, case, read, status="verified")
    admin.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, %s, %s)",
        (case, recheck, RECHECK, world["expert"]),
    )
    on_unread = add_fact(admin, case, unread, status="verified")
    reworded = add_fact(admin, case, read, status="edited", value="12 ug/L")
    moved = add_fact(admin, case, read, status="verified")
    newer = add_fact(admin, case, read, status="verified")
    gone = uuid.uuid4()

    o1 = await save_opinion(
        aconn, case, world["q1"], "[[cite:1]] [[cite:2]] [[cite:3]]",
        [cite(ok, read), cite(rejected, read), cite(unchecked, read)],
    )  # fmt: skip
    o2 = await save_opinion(
        aconn, case, world["q2"], " ".join(f"[[cite:{n}]]" for n in range(1, 8)),
        [
            cite(recheck, read), cite(on_unread, unread), cite(reworded, read), cite(moved, unread),
            cite(newer, read, version=99), cite(gone, read), cite(ok, read, version=None),
        ],
    )  # fmt: skip

    async with api_db.request(scope_for(world["expert"])) as conn:
        found = await citations.citation_problems(conn, case, PageReads())

    assert [(p.question_id, p.opinion_id, p.citation, p.fact_id, p.kind) for p in found] == [
        (world["q1"], o1, 2, rejected, "not_citable"),
        (world["q1"], o1, 3, unchecked, "not_citable"),
        (world["q2"], o2, 1, recheck, "not_citable"),
        (world["q2"], o2, 2, on_unread, "not_citable"),
        (world["q2"], o2, 3, reworded, "changed_since_cited"),
        (world["q2"], o2, 4, moved, "changed_since_cited"),
        (world["q2"], o2, 5, newer, "changed_since_cited"),
        (world["q2"], o2, 6, gone, "missing"),
    ]

    async with api_db.request(scope_for(world["outsider"])) as conn:
        assert await citations.citation_problems(conn, case, PageReads()) == []


async def test_only_the_latest_save_is_checked_and_a_clean_case_has_no_problems(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case, read = world["case"], world["read"]
    ok = add_fact(admin, case, read, status="verified", value="ferritin 9 ug/L")
    rejected = add_fact(admin, case, read, status="rejected")
    await save_opinion(aconn, case, world["q1"], "[[cite:1]]", [cite(rejected, read)])
    await save_opinion(aconn, case, world["q1"], "[[cite:1]]", [cite(ok, read)])
    await save_opinion(aconn, case, world["q2"], "No citations here.", [])

    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await citations.citation_problems(conn, case, PageReads()) == []
        closed = await citations.citation_problems(conn, case, None)
    assert [(p.fact_id, p.kind) for p in closed] == [(ok, "not_citable")], "no read log: nothing is citable"


async def test_the_latest_opinion_on_each_question_opens_in_question_order(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case, read = world["case"], world["read"]
    ok = add_fact(admin, case, read, status="verified", value="ferritin 9 ug/L")
    await save_opinion(aconn, case, world["q2"], "Zz second question.", [])
    await save_opinion(aconn, case, world["q1"], "Zz draft [[cite:1]]", [cite(ok, read)])
    o1 = await save_opinion(
        aconn, case, world["q1"], "Zz final [[cite:1]]", [cite(ok, read, version=2)], test_text="Zz test"
    )

    async with api_db.request(scope_for(world["expert"])) as conn:
        found = await opinions.latest_opinions(conn, case, CRYPTO)

    assert [(o.question_id, o.version, o.body, o.test_text) for o in found] == [
        (world["q1"], 2, "Zz final [[cite:1]]", "Zz test"),
        (world["q2"], 1, "Zz second question.", None),
    ]
    first = found[0]
    assert first.opinion_id == o1
    assert first.view == "zz_view"
    assert first.provisional is False
    assert first.citations == [citations.Citation(fact_id=ok, page_id=read, quote_verbatim="9 ug/L", fact_version=2)]

    async with api_db.request(scope_for(world["outsider"])) as conn:
        assert await opinions.latest_opinions(conn, case, CRYPTO) == []
