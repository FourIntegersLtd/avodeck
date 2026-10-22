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
from tests.support.facts_seed import insert_fact
from tests.support.packs import BOTH, LAUNCH, Combo
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
    # The stub facts are the launch packs' kinds, so the case is under the launch packs.
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
    string_key = next(k for k, t in kind_fields(LAUNCH)[second["kind"]].items() if t == "string")
    new_fields = {string_key: first["value"]}

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


async def test_a_please_check_fact_whose_words_are_gone_cannot_be_confirmed_as_it_was(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    """Rule 2: after a re-read the old word ids may point at nothing or at other text."""
    client = world["client"]
    await read(client, world)
    fact = next(f for f in await facts(client, world) if f["value"] and f["word_ids"])
    verified = (await act(client, world, fact, action="correct")).json()
    recheck(admin, world, fact["id"])
    admin.execute(
        "delete from app.ocr_words where page_id = %s and word_id = any(%s)", (world["page_id"], fact["word_ids"])
    )
    again = await act(client, world, verified, action="correct")
    assert again.status_code == 422, again.text
    shown = {f["id"]: f for f in await facts(client, world)}[fact["id"]]
    assert shown["needs_recheck"] is True and shown["version"] == verified["version"]


async def test_a_please_check_fact_can_be_changed_to_cite_the_words_on_the_page_now(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = next(f for f in await facts(client, world) if f["value"] and f["word_ids"])
    verified = (await act(client, world, fact, action="correct")).json()
    recheck(admin, world, fact["id"])
    changed = await act(
        client, world, verified, action="edit", new_value=fact["value"].upper(), word_ids=fact["word_ids"]
    )
    assert changed.status_code == 200, changed.text
    assert (changed.json()["status"], changed.json()["needs_recheck"]) == ("edited", False)


async def test_a_please_check_fact_cannot_be_changed_without_choosing_words(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    fact = next(f for f in await facts(client, world) if f["value"] and f["word_ids"])
    verified = (await act(client, world, fact, action="correct")).json()
    recheck(admin, world, fact["id"])
    assert (await act(client, world, verified, action="edit", new_value=fact["value"])).status_code == 422


# Changed details are checked against the case's pack -------------------------------------------------

GOOD = {"string": "Alpha", "integer": 3, "number": 2.5, "date": "2024-05-15", "boolean": True}
WRONG = {"string": 123, "integer": "many", "number": "lots", "date": "not a date", "boolean": "yes"}


def kind_fields(combo: Combo) -> dict[str, dict[str, str]]:
    """Each fact kind's fields, from the pack file as written (rule 11: never typed into the test)."""
    return {k["key"]: dict(k.get("fields") or {}) for k in combo.raw("specialty", "facts.yaml")["fact_kinds"]}


@pytest.fixture(params=BOTH, ids=lambda c: c.specialty)
async def pack_world(
    request: pytest.FixtureRequest,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    client_for: Any,
) -> dict[str, Any]:
    """A case under one pack set with a suggested fact of the pack's first kind that has fields."""
    combo: Combo = request.param
    expert = seed.person()
    case = seed.case(
        seed.org(kind="personal", members={expert: "owner"}), expert, status="active", packs=combo.case_row()
    )
    page = seed.page(case, seed.document(case), 7)
    sealed = await CRYPTO.encrypt(aconn, case, b"Alpha", purpose=word_purpose(page, "w1"))
    admin.execute(
        "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
        " values (%s, %s, 1, 'w1', %s, '{10,10,60,10,60,30,10,30}', 0.99, 'ocr')",
        (page, case, sealed),
    )
    kind, fields = next((k, f) for k, f in kind_fields(combo).items() if f)
    fact_id = insert_fact(admin, case, page, kind, value="Alpha", word_ids=["w1"], status="suggested")
    w = {"case": case, "client": await client_for(expert), "fact_id": fact_id, "fields": fields, "combo": combo}
    await read(w["client"], w, 7)
    return w


async def the_fact(w: dict[str, Any]) -> dict[str, Any]:
    return next(f for f in await facts(w["client"], w, 7) if f["id"] == str(w["fact_id"]))


async def test_a_change_naming_a_detail_the_pack_does_not_have_is_refused(pack_world: dict[str, Any]) -> None:
    fact = await the_fact(pack_world)
    response = await act(
        pack_world["client"], pack_world, fact, action="edit", new_value="Alpha", fields={"zz_not_a_field": "Alpha"}
    )
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == "invalid_fields"
    assert (await the_fact(pack_world))["version"] == fact["version"]


async def test_a_detail_of_the_wrong_type_is_refused(pack_world: dict[str, Any]) -> None:
    """Each field type the pack uses: a value of another type is 422 (a date must be a real date)."""
    fact = await the_fact(pack_world)
    for key, kind_type in pack_world["fields"].items():
        response = await act(
            pack_world["client"], pack_world, fact, action="edit", new_value="Alpha", fields={key: WRONG[kind_type]}
        )
        assert response.status_code == 422, (key, kind_type, response.text)
        assert response.json()["error"]["code"] == "invalid_fields"


async def test_details_of_the_right_names_and_types_are_kept(
    admin: psycopg.Connection, pack_world: dict[str, Any]
) -> None:
    sent = {key: GOOD[kind_type] for key, kind_type in pack_world["fields"].items()}
    fact = await the_fact(pack_world)
    response = await act(pack_world["client"], pack_world, fact, action="edit", new_value="Alpha", fields=sent)
    assert response.status_code == 200, response.text
    assert response.json()["fields"] == sent
    row = admin.execute("select fields from app.facts where id = %s", (pack_world["fact_id"],)).fetchone()
    assert row is not None and row[0] == sent


async def test_edit_can_change_fields_without_resending_the_headline(pack_world: dict[str, Any]) -> None:
    """One wrong detail line: fields alone may be sent; value and word_ids stay as they were."""
    fact = await the_fact(pack_world)
    key = next(iter(pack_world["fields"]))
    kind_type = pack_world["fields"][key]
    new_line = GOOD[kind_type] if GOOD[kind_type] != (fact.get("fields") or {}).get(key) else "Updated detail"
    sent = {**(fact.get("fields") or {}), key: new_line}
    response = await act(pack_world["client"], pack_world, fact, action="edit", fields=sent)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "edited"
    assert body["value"] == fact["value"]
    assert body["word_ids"] == fact["word_ids"]
    assert body["fields"][key] == new_line


async def test_a_wrong_date_or_yes_no_is_refused_under_the_launch_packs(
    seed: Seed, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, client_for: Any
) -> None:
    """The launch packs use `date` and `boolean` fields; both are checked."""
    kinds = kind_fields(LAUNCH)
    kind = next(k for k, f in kinds.items() if "date" in f.values() and "boolean" in f.values())
    date_key = next(k for k, t in kinds[kind].items() if t == "date")
    bool_key = next(k for k, t in kinds[kind].items() if t == "boolean")
    expert = seed.person()
    case = seed.case(
        seed.org(kind="personal", members={expert: "owner"}), expert, status="active", packs=LAUNCH.case_row()
    )
    page = seed.page(case, seed.document(case), 3)
    sealed = await CRYPTO.encrypt(aconn, case, b"Alpha", purpose=word_purpose(page, "w1"))
    admin.execute(
        "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
        " values (%s, %s, 1, 'w1', %s, '{10,10,60,10,60,30,10,30}', 0.99, 'ocr')",
        (page, case, sealed),
    )
    fact_id = insert_fact(admin, case, page, kind, value="Alpha", word_ids=["w1"], status="suggested")
    w = {"case": case, "client": await client_for(expert)}
    await read(w["client"], w, 3)
    fact = next(f for f in await facts(w["client"], w, 3) if f["id"] == str(fact_id))
    for bad in ({date_key: "15th of May"}, {date_key: "2024-02-30"}, {bool_key: "yes"}, {bool_key: 1}):
        response = await act(w["client"], w, fact, action="edit", new_value="Alpha", fields=bad)
        assert response.status_code == 422, (bad, response.text)
    good = await act(
        w["client"], w, fact, action="edit", new_value="Alpha", fields={date_key: "2024-05-15", bool_key: False}
    )
    assert good.status_code == 200, good.text


async def test_a_please_check_change_without_words_asks_for_words_by_code(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    """The screen shows its own words for `words_needed` (choose the words on the page)."""
    client = world["client"]
    await read(client, world)
    fact = next(f for f in await facts(client, world) if f["value"] and f["word_ids"])
    verified = (await act(client, world, fact, action="correct")).json()
    recheck(admin, world, fact["id"])
    response = await act(client, world, verified, action="edit", new_value=fact["value"])
    assert response.status_code == 422 and response.json()["error"]["code"] == "words_needed"


async def test_a_suggestion_names_the_other_records_of_the_same_day_on_its_page(
    admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = world["client"]
    await read(client, world)
    listed = await facts(client, world)
    by_kind = {f["kind"]: f for f in listed if f["kind"] in ("prescription", "examination", "result")}
    related = by_kind["prescription"]["related"]
    assert {r["kind"] for r in related} == {"examination", "safety_netting"}
    assert all(r["event_date"] == "2024-05-15" and r["fact_id"] and r["status"] == "suggested" for r in related)
    assert by_kind["result"]["related"] == []  # same day, same kind: not another record
    exam = next(r for r in related if r["kind"] == "examination")
    assert exam["value"] == "64.2 kg"

    prescription = by_kind["prescription"]
    exam = next(f for f in listed if f["kind"] == "examination")
    assert (await act(client, world, exam, action="correct")).status_code == 200
    again = {f["kind"]: f for f in await facts(client, world)}
    assert next(r for r in again["prescription"]["related"] if r["kind"] == "examination")["status"] == "verified"
    assert prescription["id"] == again["prescription"]["id"]
