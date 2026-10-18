"""Slice B, screen S09: checking a suggestion (architecture 5, 09 Reader; state-machines facts; rule 2).

- `POST /v1/cases/{id}/facts/{fact_id}/verify` with If-Match `{action, new_value?, word_ids?, reason?}`:
  `correct` → verified, `not_right` → rejected (optional short reason), `edit` → edited when the new
  value is found in the cited words, or in newly chosen words on the same page; otherwise 422.
  Only the named expert checks (403 for anyone else on the case); 423 `blind_read` until the expert has
  read the page. Every check appends a `fact_verifications` row and the fact's version goes up.
  A change the fact's state does not allow is 409.
- `POST /v1/cases/{id}/facts/{fact_id}/flag-point` / `DELETE` the same: send a checked fact to Points
  against, or take it back. Unchecked facts cannot be sent (409). Slice E reads the flags through
  `app.core.contracts.fact_flags.flagged_fact_ids`.
- `POST /v1/cases/{id}/facts/{fact_id}/move` `{to_page_no, word_ids}`: move a fact to the clearer copy
  of its page, citing that page's words. Any other page is 409; the clearer copy must be read first.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.contracts.fact_flags import flagged_fact_ids
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.http import etag
from app.features.pipeline.paths import word_purpose
from tests.fixtures.stub_facts import load_stub_facts
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


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
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO)
    return {
        "case": case,
        "expert": expert,
        "assistant": assistant,
        "page_id": loaded[118].page_id,
        "client": await client_for(expert),
    }


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def read(client: httpx.AsyncClient, w: dict[str, Any], n: int = 118) -> None:
    assert (await client.post(f"{base(w)}/pages/{n}/read", json={"dwell_seconds": 20})).status_code == 200


async def facts(client: httpx.AsyncClient, w: dict[str, Any], n: int = 118) -> list[dict[str, Any]]:
    response = await client.get(f"{base(w)}/pages/{n}/facts")
    assert response.status_code == 200, response.text
    return response.json()["facts"]  # type: ignore[no-any-return]


async def act(
    client: httpx.AsyncClient, w: dict[str, Any], fact: dict[str, Any], what: str = "verify", **body: Any
) -> httpx.Response:
    return await client.post(
        f"{base(w)}/facts/{fact['id']}/{what}", json=body, headers={"if-match": etag(fact["version"])}
    )


def log(admin: psycopg.Connection, fact_id: str) -> list[tuple[Any, ...]]:
    return admin.execute(
        "select action, person_id, old_value, new_value, old_word_ids, new_word_ids, reason, old_page_id, new_page_id"
        " from app.fact_verifications where fact_id = %s order by seq",
        (fact_id,),
    ).fetchall()


async def test_a_suggestion_on_an_unread_page_cannot_be_checked(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    fact_id = admin.execute(
        "select id, version from app.facts where page_id = %s limit 1", (world["page_id"],)
    ).fetchone()
    assert fact_id is not None
    response = await act(world["client"], world, {"id": fact_id[0], "version": fact_id[1]}, action="correct")
    assert response.status_code == 423 and response.json()["error"]["code"] == "blind_read"
    assert log(admin, fact_id[0]) == []


async def test_correct_verifies_the_fact_and_logs_who_checked_it(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]

    response = await act(client, world, fact, action="correct")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "verified" and body["version"] == fact["version"] + 1
    assert response.headers["etag"] == etag(body["version"])
    assert log(admin, fact["id"]) == [
        ("correct", world["expert"], fact["value"], fact["value"], fact["word_ids"], fact["word_ids"], None, None, None)
    ]


async def test_not_right_rejects_it_with_an_optional_reason_and_can_be_undone(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]

    rejected = await act(client, world, fact, action="not_right", reason="wrong_page")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    shown = {f["id"]: f for f in await facts(client, world)}
    assert shown[fact["id"]]["status"] == "rejected", "a rejected suggestion stays on the page so it can be undone"
    again = await act(client, world, shown[fact["id"]], action="correct")
    assert again.status_code == 200 and again.json()["status"] == "verified"
    assert [(r[0], r[6]) for r in log(admin, fact["id"])] == [("not_right", "wrong_page"), ("correct", None)]


async def test_an_edit_must_be_found_in_the_words_it_cites(admin: psycopg.Connection, world: dict[str, Any]) -> None:
    client = world["client"]
    await read(client, world)
    first, second = [f for f in await facts(client, world) if f["value"]][:2]
    assert first["value"].lower() != second["value"].lower()

    refused = await act(client, world, first, action="edit", new_value=second["value"])
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "invalid"

    same_words = await act(client, world, first, action="edit", new_value=first["value"].upper())
    assert same_words.status_code == 200 and same_words.json()["status"] == "edited"
    assert same_words.json()["value"] == first["value"].upper()

    other_words = await act(client, world, second, action="edit", new_value=first["value"], word_ids=first["word_ids"])
    assert other_words.status_code == 200
    assert other_words.json()["word_ids"] == first["word_ids"]
    assert log(admin, second["id"])[0][:6] == (
        "edit",
        world["expert"],
        second["value"],
        first["value"],
        second["word_ids"],
        first["word_ids"],
    )


async def test_words_from_another_page_are_refused(seed: Seed, world: dict[str, Any]) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    response = await act(client, world, fact, action="edit", new_value=fact["value"], word_ids=["no_such_word"])
    assert response.status_code == 422


async def test_a_change_the_state_does_not_allow_is_409(world: dict[str, Any]) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    verified = (await act(client, world, fact, action="correct")).json()
    assert (await act(client, world, verified, action="correct")).status_code == 409
    assert (await act(client, world, verified, action="edit", new_value=fact["value"])).status_code == 409


async def test_only_the_named_expert_checks_and_versions_must_match(client_for: Any, world: dict[str, Any]) -> None:
    assistant = await client_for(world["assistant"])
    await read(assistant, world)
    fact = (await facts(assistant, world))[0]
    assert (await act(assistant, world, fact, action="correct")).status_code == 403

    client = world["client"]
    await read(client, world)
    stale = await act(client, world, {**fact, "version": fact["version"] + 5}, action="correct")
    assert stale.status_code == 412
    bare = await client.post(f"{base(world)}/facts/{fact['id']}/verify", json={"action": "correct"})
    assert bare.status_code == 428


async def test_a_checked_fact_goes_to_points_against_and_back(
    api_db: Database, client_for: Any, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    assert (await act(client, world, fact, "flag-point")).status_code == 409, "only checked facts are sent"

    verified = (await act(client, world, fact, action="correct")).json()
    for _ in range(2):
        sent = await act(client, world, verified, "flag-point")
        assert sent.status_code == 200 and sent.json()["flagged"] is True
    shown = {f["id"]: f for f in await facts(client, world)}
    assert shown[fact["id"]]["flagged"] is True
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await flagged_fact_ids(conn, world["case"], "points") == {uuid.UUID(fact["id"])}

    assistant = await client_for(world["assistant"])
    assert (await assistant.post(f"{base(world)}/facts/{fact['id']}/flag-point")).status_code == 403

    taken_back = await client.delete(f"{base(world)}/facts/{fact['id']}/flag-point")
    assert taken_back.status_code == 200 and taken_back.json()["flagged"] is False
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await flagged_fact_ids(conn, world["case"], "points") == set()


async def clearer_copy(
    seed: Seed, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, w: dict[str, Any], number: int
) -> uuid.UUID:
    """A clearer copy of page 118 with the same words, sealed for the new page."""
    doc = admin.execute("select document_id from app.pages where id = %s", (w["page_id"],)).fetchone()
    assert doc is not None
    new = seed.page(w["case"], doc[0], number)
    admin.execute("update app.pages set superseded_by_page_id = %s where id = %s", (new, w["page_id"]))
    for n, word_id, text, polygon, source in admin.execute(
        "select n, word_id, text, polygon, source from app.ocr_words where page_id = %s order by n", (w["page_id"],)
    ).fetchall():
        plain = await CRYPTO.decrypt(aconn, w["case"], bytes(text), purpose=word_purpose(w["page_id"], word_id))
        sealed = await CRYPTO.encrypt(aconn, w["case"], plain, purpose=word_purpose(new, word_id))
        admin.execute(
            "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
            " values (%s, %s, %s, %s, %s, %s, 0.99, %s)",
            (new, w["case"], n, word_id, sealed, polygon, source),
        )
    return new


async def test_a_fact_moves_to_the_clearer_copy_and_nowhere_else(
    seed: Seed, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    new = await clearer_copy(seed, admin, aconn, world, 901)
    seed.page(world["case"], seed.document(world["case"]), 902)

    unread = await act(client, world, fact, "move", to_page_no=901, word_ids=fact["word_ids"])
    assert unread.status_code == 423
    await read(client, world, 901)
    await read(client, world, 902)
    elsewhere = await act(client, world, fact, "move", to_page_no=902, word_ids=fact["word_ids"])
    assert elsewhere.status_code == 409

    moved = await act(client, world, fact, "move", to_page_no=901, word_ids=fact["word_ids"])
    assert moved.status_code == 200, moved.text
    assert moved.json()["version"] == fact["version"] + 1
    assert [f["id"] for f in await facts(client, world, 901)] == [fact["id"]]
    assert fact["id"] not in [f["id"] for f in await facts(client, world)]
    row = log(admin, fact["id"])[-1]
    assert (row[0], row[7], row[8]) == ("move", world["page_id"], new)


async def test_a_change_updates_the_details_and_box_and_logs_old_and_new(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    """Change a detail: `fields` and `event_date` change when sent, the box follows the chosen words,
    and the log keeps the real old and new details."""
    admin.execute(
        "insert into app.page_analysis (page_id, case_id, width, height) values (%s, %s, 1000, 1400)"
        " on conflict (page_id) do update set width = 1000, height = 1400",
        (world["page_id"], world["case"]),
    )
    client = world["client"]
    await read(client, world)
    first, second = [f for f in await facts(client, world) if f["value"]][:2]
    new_fields = {"note": first["value"]}

    response = await act(
        client,
        world,
        second,
        action="edit",
        new_value=first["value"],
        word_ids=first["word_ids"],
        fields=new_fields,
        event_date="2019-03-04",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["fields"] == new_fields and body["event_date"] == "2019-03-04"
    assert body["bbox"] is not None and body["bbox"] != second["bbox"]
    for side in ("x0", "y0", "x1", "y1"):
        assert abs(body["bbox"][side] - first["bbox"][side]) < 0.01, side
    row = admin.execute(
        "select old_fields, new_fields, old_value, new_value from app.fact_verifications where fact_id = %s",
        (second["id"],),
    ).fetchone()
    assert row == (second["fields"], new_fields, second["value"], first["value"])


async def test_a_change_without_details_keeps_them(admin: psycopg.Connection, world: dict[str, Any]) -> None:
    client = world["client"]
    await read(client, world)
    fact = next(f for f in await facts(client, world) if f["value"])
    body = (await act(client, world, fact, action="edit", new_value=fact["value"].upper())).json()
    assert (body["fields"], body["event_date"]) == (fact["fields"], fact["event_date"])


def recheck(admin: psycopg.Connection, w: dict[str, Any], fact_id: str) -> None:
    admin.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, 'recheck', null)",
        (w["case"], fact_id),
    )


async def test_a_please_check_flag_is_not_points_against_and_goes_when_checked_again(
    admin: psycopg.Connection, api_db: Database, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    verified = (await act(client, world, fact, action="correct")).json()
    recheck(admin, world, fact["id"])

    shown = {f["id"]: f for f in await facts(client, world)}[fact["id"]]
    assert (shown["flagged"], shown["needs_recheck"]) == (False, True), "a recheck is not in Points against"
    assert (await client.delete(f"{base(world)}/facts/{fact['id']}/flag-point")).status_code == 200
    assert {f["id"]: f for f in await facts(client, world)}[fact["id"]]["needs_recheck"] is True

    sent = (await act(client, world, shown, "flag-point")).json()
    assert sent["flagged"] is True
    shown = {f["id"]: f for f in await facts(client, world)}[fact["id"]]
    assert (shown["flagged"], shown["needs_recheck"]) == (True, True)

    again = await act(client, world, shown, action="correct")
    assert again.status_code == 200, again.text
    assert again.json()["status"] == verified["status"]
    assert (again.json()["flagged"], again.json()["needs_recheck"]) == (True, False)
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await flagged_fact_ids(conn, world["case"], "recheck") == set()
        assert await flagged_fact_ids(conn, world["case"], "points") == {uuid.UUID(fact["id"])}
    assert [r[0] for r in log(admin, fact["id"])] == ["correct", "correct"]


async def test_a_checked_fact_with_no_please_check_flag_still_cannot_be_correct_again(world: dict[str, Any]) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    verified = (await act(client, world, fact, action="correct")).json()
    assert (await act(client, world, verified, action="correct")).status_code == 409


async def test_rejecting_a_fact_flagged_please_check_takes_the_flag_off(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = (await facts(client, world))[0]
    verified = (await act(client, world, fact, action="correct")).json()
    recheck(admin, world, fact["id"])
    rejected = await act(client, world, verified, action="not_right")
    assert rejected.status_code == 200 and rejected.json()["needs_recheck"] is False
