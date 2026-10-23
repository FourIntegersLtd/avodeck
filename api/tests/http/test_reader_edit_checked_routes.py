"""Slice B, screen S09: changing a fact the expert has already checked (state-machines "Fact"; rules 2 and 8).

- `POST /v1/cases/{id}/facts/{fact_id}/verify` `edit` on a `verified` or `edited` fact → `edited`. The new
  value passes the same provenance check as any change (422 when the chosen words do not show it, the
  expert's reading of misread words included), and `fact_verifications` keeps the old value.
- A fact cited in a served report (rule 8) is refused with 409 `served_use_supplementary`: the change
  goes through the supplementary report (screen 21b). Nothing changes.
- A fact cited in an unserved opinion or draft report may change; its version rises, so the opinion's
  citation shows as `changed_since_cited` in `core/contracts/citations.citation_problems` (architecture
  10.4) and the final check fails until the expert reviews it.
- `GET /v1/cases/{id}/pages/{n}/words` also gives `reading`: the page's words as one text in reading
  order, with the expert's readings of misread words in place (to start typing out the page from).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from app.core.contracts import citations
from app.core.contracts.reading import PageReads
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.http import etag
from tests.fixtures.stub_facts import load_stub_facts
from tests.support.contracts import add_question, add_report_version
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
READING = "Tetanus booster given"


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    from tests.support.files import build_app

    return build_app(api_db, auth_env, crypto=CRYPTO)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def world(seed: Seed, aconn: psycopg.AsyncConnection, client_for: Any) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(
        seed.org(kind="personal", members={expert: "owner"}), expert, status="active", packs=LAUNCH.case_row()
    )
    loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO)
    client = await client_for(expert)
    w = {"case": case, "expert": expert, "page_id": loaded[118].page_id, "client": client}
    assert (await client.post(f"{base(w)}/pages/118/read", json={"dwell_seconds": 20})).status_code == 200
    return w


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def facts(w: dict[str, Any]) -> list[dict[str, Any]]:
    response = await w["client"].get(f"{base(w)}/pages/118/facts")
    assert response.status_code == 200, response.text
    return response.json()["facts"]  # type: ignore[no-any-return]


async def verify(w: dict[str, Any], fact: dict[str, Any], **body: Any) -> httpx.Response:
    return await w["client"].post(  # type: ignore[no-any-return]
        f"{base(w)}/facts/{fact['id']}/verify", json=body, headers={"if-match": etag(fact["version"])}
    )


async def checked(w: dict[str, Any]) -> dict[str, Any]:
    """A fact with a value, checked as correct."""
    fact = next(f for f in await facts(w) if f["value"] and f["word_ids"])
    response = await verify(w, fact, action="correct")
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def history(admin: psycopg.Connection, fact_id: str) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select action, old_value, new_value from app.fact_verifications where fact_id = %s order by seq", (fact_id,)
    ).fetchall()


def cite_in_report(admin: psycopg.Connection, w: dict[str, Any], fact: dict[str, Any], *, served: bool) -> None:
    """A report version citing the fact (citations are written on a draft; serving locks it)."""
    version = add_report_version(admin, w["case"], version_no=7)
    paragraph = admin.execute(
        "insert into app.report_paragraphs (case_id, report_version_id, para_no, section, content_control_id, text)"
        " values (%s, %s, 1, 'opinion', 'cc1', %s) returning id",
        (w["case"], version, b"sealed"),
    ).fetchone()
    assert paragraph is not None
    admin.execute(
        "insert into app.citations (case_id, report_version_id, paragraph_id, cite_no, fact_id, fact_version,"
        " value_snapshot, quote_verbatim, page_id) values (%s, %s, %s, 1, %s, %s, %s, %s, %s)",
        (w["case"], version, paragraph[0], fact["id"], fact["version"], b"sealed", b"sealed", w["page_id"]),
    )
    if served:
        admin.execute(
            "update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("a" * 64, version)
        )
        admin.execute("update app.report_versions set status = 'served' where id = %s", (version,))


async def test_a_checked_fact_can_be_changed_and_keeps_its_old_value(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact = await checked(world)
    changed = await verify(world, fact, action="edit", new_value=fact["value"].upper())
    assert changed.status_code == 200, changed.text
    assert (changed.json()["status"], changed.json()["value"]) == ("edited", fact["value"].upper())
    assert changed.json()["version"] > fact["version"]
    assert history(admin, fact["id"])[-1] == ("edit", fact["value"], fact["value"].upper())

    again = await verify(world, changed.json(), action="edit", new_value=fact["value"])
    assert again.status_code == 200, again.text
    assert again.json()["status"] == "edited"
    assert history(admin, fact["id"])[-1] == ("edit", fact["value"].upper(), fact["value"])


async def test_a_checked_fact_can_get_a_timeline_date(world: dict[str, Any]) -> None:
    fact = await checked(world)
    changed = await verify(world, fact, action="edit", event_date="2024-05-15")
    assert changed.status_code == 200, changed.text
    assert changed.json()["event_date"] == "2024-05-15"


async def test_a_change_to_a_checked_fact_not_in_its_words_is_still_refused(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact = await checked(world)
    refused = await verify(world, fact, action="edit", new_value="Something the page does not say")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid"
    assert history(admin, fact["id"])[-1][0] == "correct", "nothing was changed"


async def test_a_checked_fact_can_cite_the_experts_reading_of_misread_words(world: dict[str, Any]) -> None:
    fact = await checked(world)
    refused = await verify(world, fact, action="edit", new_value=READING, word_ids=fact["word_ids"])
    assert refused.status_code == 422
    changed = await verify(
        world,
        fact,
        action="edit",
        new_value=READING,
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": fact["word_ids"], "text": READING},
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["value"], changed.json()["cites_correction"]) == (READING, True)


async def test_a_fact_cited_in_a_served_report_is_changed_through_the_supplementary_report(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact = await checked(world)
    cite_in_report(admin, world, fact, served=True)
    refused = await verify(world, fact, action="edit", new_value=fact["value"].upper())
    assert refused.status_code == 409, refused.text
    assert refused.json()["error"]["code"] == "served_use_supplementary"
    now = next(f for f in await facts(world) if f["id"] == fact["id"])
    assert (now["status"], now["value"], now["version"]) == ("verified", fact["value"], fact["version"])


async def test_a_fact_cited_only_in_a_draft_report_may_change(admin: psycopg.Connection, world: dict[str, Any]) -> None:
    fact = await checked(world)
    cite_in_report(admin, world, fact, served=False)
    changed = await verify(world, fact, action="edit", new_value=fact["value"].upper())
    assert changed.status_code == 200, changed.text


async def test_a_change_to_a_fact_cited_in_an_opinion_flags_the_citation_as_changed(
    api_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact = await checked(world)
    question = add_question(admin, world["case"], number=1, status="confirmed")
    ref = {
        "fact_id": fact["id"],
        "page_id": str(world["page_id"]),
        "quote_verbatim": fact["value"],
        "fact_version": fact["version"],
    }
    admin.execute(
        "insert into app.opinions (case_id, question_id, body, citation_refs, view) values (%s, %s, %s, %s, 'zz_view')",
        (world["case"], question, b"sealed", Jsonb([ref])),
    )
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await citations.citation_problems(conn, world["case"], PageReads()) == []

    changed = await verify(world, fact, action="edit", new_value=fact["value"].upper())
    assert changed.status_code == 200, changed.text

    async with api_db.request(scope_for(world["expert"])) as conn:
        problems = await citations.citation_problems(conn, world["case"], PageReads())
    assert [(p.fact_id, p.kind) for p in problems] == [(uuid.UUID(fact["id"]), "changed_since_cited")]


async def test_the_words_give_the_pages_reading_with_the_experts_corrections(world: dict[str, Any]) -> None:
    fact = await checked(world)
    before = (await world["client"].get(f"{base(world)}/pages/118/words")).json()
    assert before["reading"] == " ".join(w["text"] for w in before["words"] if w["text"])
    changed = await verify(
        world,
        fact,
        action="edit",
        new_value=READING,
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": fact["word_ids"], "text": READING},
    )
    assert changed.status_code == 200, changed.text
    after = (await world["client"].get(f"{base(world)}/pages/118/words")).json()
    assert READING in after["reading"]
    assert READING not in before["reading"]
