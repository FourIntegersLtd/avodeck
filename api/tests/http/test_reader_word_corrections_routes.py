"""Slice B, screen S09: correcting the computer's reading of some words (decision 0018, rule 2).

- `POST /v1/cases/{id}/facts/{fact_id}/verify` `edit` may carry `corrected_words {word_ids, text}`: the
  expert says what those words on the page really say. The corrected words must be among the words the
  fact cites, in one unbroken run. The value must then be found in the cited words with the expert's
  reading in place of the computer's; without the correction the same value is refused as before.
- The computer's reading stays in `ocr_words` untouched; the correction is kept sealed under the case key
  in `word_corrections`; the fact shows `cites_correction` ("Read by you").
- A later correction of any of the same words replaces the earlier one (`superseded_at`), and any other
  checked fact on those words that the new reading no longer shows is flagged "Please check".
- Only the named expert corrects (403 for anyone else); 423 `blind_read` until the page is read.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.http import etag
from tests.fixtures.stub_facts import load_stub_facts
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
READING = "tetanus booster given third dose"


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
    expert, assistant = seed.person(), seed.person()
    case = seed.case(
        seed.org(kind="personal", members={expert: "owner"}), expert, status="active", packs=LAUNCH.case_row()
    )
    seed.case_member(case, assistant, "assistant", added_by=expert)
    loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO)
    return {
        "case": case,
        "expert": expert,
        "assistant": assistant,
        "page_id": loaded[118].page_id,
        "client": await client_for(expert),
        "assistant_client": await client_for(assistant),
    }


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def read(client: httpx.AsyncClient, w: dict[str, Any]) -> None:
    assert (await client.post(f"{base(w)}/pages/118/read", json={"dwell_seconds": 20})).status_code == 200


async def facts(client: httpx.AsyncClient, w: dict[str, Any]) -> list[dict[str, Any]]:
    response = await client.get(f"{base(w)}/pages/118/facts")
    assert response.status_code == 200, response.text
    return [f for f in response.json()["facts"] if f["value"]]


async def edit(client: httpx.AsyncClient, w: dict[str, Any], fact: dict[str, Any], **body: Any) -> httpx.Response:
    return await client.post(
        f"{base(w)}/facts/{fact['id']}/verify",
        json={"action": "edit", **body},
        headers={"if-match": etag(fact["version"])},
    )


def corrections(admin: psycopg.Connection, w: dict[str, Any]) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select word_ids, text, person_id, superseded_at from app.word_corrections where page_id = %s"
        " order by created_at, id",
        (w["page_id"],),
    ).fetchall()


def ocr_text(admin: psycopg.Connection, w: dict[str, Any]) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select word_id, text from app.ocr_words where page_id = %s order by n", (w["page_id"],)
    ).fetchall()


async def test_a_misread_reading_is_corrected_and_the_value_cites_the_correction(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    words_before = ocr_text(admin, world)

    response = await edit(
        client,
        world,
        fact,
        new_value="Tetanus booster given",
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": fact["word_ids"], "text": READING},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "edited" and body["value"] == "Tetanus booster given"
    assert body["cites_correction"] is True
    shown = {f["id"]: f for f in await facts(client, world)}
    assert shown[fact["id"]]["cites_correction"] is True
    assert all(f["cites_correction"] is False for i, f in shown.items() if i != fact["id"])

    assert ocr_text(admin, world) == words_before, "the computer's reading stays as it was"
    saved = corrections(admin, world)
    assert len(saved) == 1
    word_ids, sealed, person, superseded = saved[0]
    assert word_ids == fact["word_ids"] and person == world["expert"] and superseded is None
    assert READING.encode() not in bytes(sealed), "the expert's reading is sealed under the case key"
    old = admin.execute(
        "select old_value, new_value from app.fact_verifications where fact_id = %s", (fact["id"],)
    ).fetchone()
    assert old == (fact["value"], "Tetanus booster given")


async def test_without_the_correction_the_same_value_is_still_refused(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    refused = await edit(client, world, fact, new_value="Tetanus booster given", word_ids=fact["word_ids"])
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid"
    assert corrections(admin, world) == []


async def test_the_correction_must_say_the_value_too(admin: psycopg.Connection, world: dict[str, Any]) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    refused = await edit(
        client,
        world,
        fact,
        new_value="Influenza vaccine",
        corrected_words={"word_ids": fact["word_ids"], "text": READING},
    )
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid"
    assert corrections(admin, world) == []


async def test_corrected_words_must_be_among_the_cited_words(admin: psycopg.Connection, world: dict[str, Any]) -> None:
    client = world["client"]
    await read(client, world)
    first, second = (await facts(client, world))[:2]
    elsewhere = await edit(
        client,
        world,
        first,
        new_value="Tetanus booster given",
        word_ids=first["word_ids"],
        corrected_words={"word_ids": second["word_ids"], "text": READING},
    )
    assert elsewhere.status_code == 422 and elsewhere.json()["error"]["code"] == "invalid"
    assert corrections(admin, world) == []


async def test_corrected_words_must_be_one_unbroken_run(admin: psycopg.Connection, world: dict[str, Any]) -> None:
    client = world["client"]
    await read(client, world)
    fact = next(f for f in await facts(client, world) if len(f["word_ids"]) >= 3)
    broken = [fact["word_ids"][0], fact["word_ids"][2]]
    refused = await edit(
        client,
        world,
        fact,
        new_value="Tetanus booster given",
        word_ids=fact["word_ids"],
        corrected_words={"word_ids": broken, "text": READING},
    )
    assert refused.status_code == 422
    assert corrections(admin, world) == []


@pytest.mark.parametrize("text", ["", "   ", "x" * 501])
async def test_the_corrected_text_must_be_given_and_short(
    admin: psycopg.Connection, world: dict[str, Any], text: str
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    refused = await edit(
        client,
        world,
        fact,
        new_value="Tetanus booster given",
        corrected_words={"word_ids": fact["word_ids"], "text": text},
    )
    assert refused.status_code == 422
    assert corrections(admin, world) == []


async def test_only_the_named_expert_corrects_and_only_after_reading(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client, other = world["client"], world["assistant_client"]
    fact_row = admin.execute(
        "select id, version, word_ids from app.facts where page_id = %s and value is not null limit 1",
        (world["page_id"],),
    ).fetchone()
    assert fact_row is not None
    fact = {"id": fact_row[0], "version": fact_row[1], "word_ids": fact_row[2]}
    body = {
        "new_value": "Tetanus booster given",
        "corrected_words": {"word_ids": fact["word_ids"], "text": READING},
    }
    unread = await edit(client, world, fact, **body)
    assert unread.status_code == 423 and unread.json()["error"]["code"] == "blind_read"
    await read(other, world)
    assert (await edit(other, world, fact, **body)).status_code == 403
    assert corrections(admin, world) == []


async def test_a_later_correction_replaces_the_earlier_and_flags_facts_it_no_longer_shows(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    first, second = (await facts(client, world))[:2]
    done = await edit(
        client,
        world,
        first,
        new_value="Tetanus booster given",
        corrected_words={"word_ids": first["word_ids"], "text": READING},
    )
    assert done.status_code == 200, done.text

    again = await edit(
        client,
        world,
        second,
        new_value="Influenza vaccine",
        word_ids=first["word_ids"],
        corrected_words={"word_ids": first["word_ids"], "text": "influenza vaccine given"},
    )
    assert again.status_code == 200, again.text
    saved = corrections(admin, world)
    assert len(saved) == 2
    assert saved[0][3] is not None and saved[1][3] is None, "only the latest reading of those words counts"
    shown = {f["id"]: f for f in await facts(client, world)}
    assert shown[first["id"]]["needs_recheck"] is True, "the new reading no longer shows the first fact"
    assert shown[second["id"]]["needs_recheck"] is False and shown[second["id"]]["cites_correction"] is True


async def test_a_later_correction_that_still_shows_a_checked_fact_leaves_it_alone(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    first, second = (await facts(client, world))[:2]
    assert (
        await edit(
            client,
            world,
            first,
            new_value="Tetanus booster given",
            corrected_words={"word_ids": first["word_ids"], "text": READING},
        )
    ).status_code == 200
    again = await edit(
        client,
        world,
        second,
        new_value="third dose",
        word_ids=first["word_ids"],
        corrected_words={"word_ids": first["word_ids"], "text": "tetanus booster given, third dose"},
    )
    assert again.status_code == 200, again.text
    shown = {f["id"]: f for f in await facts(client, world)}
    assert shown[first["id"]]["needs_recheck"] is False
