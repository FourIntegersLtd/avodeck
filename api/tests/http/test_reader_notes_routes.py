"""Slice B, screen S09: the expert's own bookmarks and private notes (approved 2026-10-23, item 10).

- `PUT /v1/cases/{id}/pages/{n}/my-note` `{bookmarked, note}` keeps, for the person and the page, a
  bookmark and a short private note (at most 2,000 characters). Clearing both removes the row.
- `GET /v1/cases/{id}/my-notes` lists the person's own bookmarks and notes, in page order. Nobody else
  sees them, not even the named expert seeing an assistant's.
- The note is sealed with the case key; it is separate from the record: keeping one is not reading the
  page (rule 3) and changes nothing on the page. It never goes to AI (the worker cannot read the table).
- Who: anyone allowed `page.read` (the named expert, co-expert, supervisor, assistant); a viewer gets
  403; anyone without access 404; an unknown page 404; without CSRF 403. Never cached.
"""

from __future__ import annotations

from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
NOTE = "Ask about the missing letter from the clinic"


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    return build_app(api_db, auth_env, crypto=CRYPTO)


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, assistant, viewer, stranger = seed.person(), seed.person(), seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    seed.case_member(case, viewer, "viewer", added_by=expert)
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    doc = seed.document(case)
    pages = {n: seed.page(case, doc, n) for n in (3, 12, 40)}
    return {
        "case": case,
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "stranger": stranger,
        "pages": pages,
    }


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def keep(client: httpx.AsyncClient, w: dict[str, Any], n: int, **body: Any) -> httpx.Response:
    return await client.put(f"{base(w)}/pages/{n}/my-note", json={"bookmarked": False, "note": "", **body})


async def mine(client: httpx.AsyncClient, w: dict[str, Any]) -> list[dict[str, Any]]:
    response = await client.get(f"{base(w)}/my-notes")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    return [{k: n[k] for k in ("page_no", "bookmarked", "note")} for n in response.json()["notes"]]


async def test_a_bookmark_and_a_note_are_kept_and_listed_in_page_order(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    saved = await keep(client, world, 40, note=NOTE)
    assert saved.status_code == 200
    assert saved.json() | {"updated_at": None} == {"page_no": 40, "bookmarked": False, "note": NOTE, "updated_at": None}
    assert (await keep(client, world, 3, bookmarked=True)).status_code == 200
    assert await mine(client, world) == [
        {"page_no": 3, "bookmarked": True, "note": ""},
        {"page_no": 40, "bookmarked": False, "note": NOTE},
    ]
    # Changing one keeps one row for the page.
    await keep(client, world, 40, bookmarked=True, note="Shorter")
    assert await mine(client, world) == [
        {"page_no": 3, "bookmarked": True, "note": ""},
        {"page_no": 40, "bookmarked": True, "note": "Shorter"},
    ]


async def test_clearing_both_removes_the_row(client_for: Any, admin: psycopg.Connection, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    await keep(client, world, 12, bookmarked=True, note=NOTE)
    cleared = await keep(client, world, 12, note="   ")
    assert cleared.status_code == 200 and cleared.json()["bookmarked"] is False and cleared.json()["note"] == ""
    assert await mine(client, world) == []
    left = admin.execute(
        "select count(*) from app.page_notes n join app.pages p on p.id = n.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and n.person_id = %s",
        (world["case"], world["expert"]),
    ).fetchone()
    assert left == (0,)


async def test_each_persons_notes_are_their_own(client_for: Any, world: dict[str, Any]) -> None:
    expert, assistant = await client_for(world["expert"]), await client_for(world["assistant"])
    await keep(expert, world, 3, bookmarked=True, note="Expert's")
    await keep(assistant, world, 3, note="Assistant's")
    assert await mine(expert, world) == [{"page_no": 3, "bookmarked": True, "note": "Expert's"}]
    assert await mine(assistant, world) == [{"page_no": 3, "bookmarked": False, "note": "Assistant's"}]


async def test_the_note_is_sealed_and_separate_from_the_record(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    await keep(client, world, 12, bookmarked=True, note=NOTE)
    (sealed,) = admin.execute(
        "select n.note from app.page_notes n join app.pages p on p.id = n.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s)"
        " and p.bundle_page_no = 12 and n.person_id = %s",
        (world["case"], world["expert"]),
    ).fetchone() or (None,)
    assert sealed is not None and NOTE.encode() not in bytes(sealed)
    reads = admin.execute("select count(*) from app.page_reads where person_id = %s", (world["expert"],)).fetchone()
    assert reads == (0,)
    facts = await client.get(f"{base(world)}/pages/12/facts")
    assert facts.status_code == 423


async def test_a_note_is_short(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    assert (await keep(client, world, 3, note="x" * 2001)).status_code == 422
    assert (await keep(client, world, 3, note="x" * 2000)).status_code == 200


async def test_who_may_keep_notes(client_for: Any, world: dict[str, Any]) -> None:
    viewer = await client_for(world["viewer"])
    assert (await keep(viewer, world, 3, bookmarked=True)).status_code == 403
    assert (await viewer.get(f"{base(world)}/my-notes")).status_code == 403
    stranger = await client_for(world["stranger"])
    assert (await keep(stranger, world, 3, bookmarked=True)).status_code == 404
    assert (await stranger.get(f"{base(world)}/my-notes")).status_code == 404
    expert = await client_for(world["expert"])
    assert (await keep(expert, world, 999, bookmarked=True)).status_code == 404
    no_csrf = await client_for(world["expert"], csrf=False)
    assert (await keep(no_csrf, world, 3, bookmarked=True)).status_code == 403
