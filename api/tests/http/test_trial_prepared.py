"""Screens 23 and 24, what the expert prepares for the day (slice I brief).

- A readiness sheet carries the expert's own reply to each question, with the date it was written; only the
  named expert writes it, and it prints on the sheet in the pack.
- A sentence of a new note found in no served document is kept, so the expert's choice (tell the instructing
  party, or leave it out) is kept and shown again.
- "Tell the instructing party" about missing pages is remembered on the court bundle.
- The pack records how many pages each section took when it was built, and the preview counts the same way.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from app.core.db import Database
from tests.fixtures.trial.fixtures import (  # noqa: F401
    Scripted,
    aconn,
    app,
    changes,
    model,
    reads,
    run_job,
    storage,
    upload_bundle,
    worker_db,
    world,
)
from tests.fixtures.trial.world import World, trial_bundle
from tests.http.test_trial_meetings import agenda, create
from tests.http.test_trial_pack import ALL, built, download, pdf_text, settings
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo

pytestmark = pytest.mark.anyio

NOTE = "The plan was recorded and repeated. Zz the patient later moved abroad."


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    chosen: Combo = request.param
    return chosen


async def prepared(client_for: ClientFor, worker_db: Database, w: World, model: Callable[..., Scripted]) -> None:
    model()
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(w.expert), worker_db, w, pdf)
    created = await create(client_for, w)
    await agenda(client_for, worker_db, w, created["id"])


async def reply(client_for: ClientFor, w: World, number: int, text: str, person: Any = None) -> Any:
    return await (await client_for(person or w.expert)).put(w.url(f"readiness-replies/{number}"), json={"reply": text})


async def sheets_of(client_for: ClientFor, w: World) -> list[dict[str, Any]]:
    body = (await (await client_for(w.expert)).get(w.url("trial-pack/preview"))).json()
    out: list[dict[str, Any]] = body["sheets"]
    return out


# --- the reply on a readiness sheet ------------------------------------------------------------------------


async def test_a_reply_is_kept_with_its_date_and_shows_on_the_question_s_sheet(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    await prepared(client_for, worker_db, world, model)
    before = await sheets_of(client_for, world)
    assert [s["reply"] for s in before] == [None, None, None] and before[1]["replied_at"] is None
    resp = await reply(client_for, world, 2, "Zz the referral was needed whatever an examination showed.")
    assert resp.status_code == 200, resp.text
    assert resp.json()["question_no"] == 2 and resp.json()["replied_at"]
    after = await sheets_of(client_for, world)
    assert after[1]["reply"] == "Zz the referral was needed whatever an examination showed."
    assert after[1]["replied_at"] and after[0]["reply"] is None
    again = await reply(client_for, world, 2, "Zz changed.")
    assert again.status_code == 200 and (await sheets_of(client_for, world))[1]["reply"] == "Zz changed."


async def test_only_the_named_expert_writes_a_reply_and_the_question_must_exist(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    await prepared(client_for, worker_db, world, model)
    assert (await reply(client_for, world, 1, "Zz no.", world.viewer)).status_code == 403
    assert (await reply(client_for, world, 1, "Zz no.", world.assistant)).status_code == 403
    assert (await reply(client_for, world, 1, "Zz no.", world.outsider)).status_code == 404
    assert (await reply(client_for, world, 9, "Zz no such question.")).status_code == 422
    assert (await reply(client_for, world, 1, "")).status_code == 422
    assert all(s["reply"] is None for s in await sheets_of(client_for, world))


async def test_the_reply_prints_on_the_readiness_sheet_in_the_pack(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    await prepared(client_for, worker_db, world, model)
    assert (await reply(client_for, world, 1, "Zz my reply to the first question.")).status_code == 200
    assert (await settings(client_for, world, sections=[*ALL, "readiness"])).status_code == 200
    await built(client_for, worker_db, world)
    assert "Zz my reply to the first question." in pdf_text(await download(client_for, world))


# --- a note found in no served document -------------------------------------------------------------------------


async def check(client_for: ClientFor, w: World, text: str = NOTE) -> list[dict[str, Any]]:
    resp = await (await client_for(w.expert)).post(w.url("trial-pack/check-new"), json={"text": text})
    assert resp.status_code == 200, resp.text
    out: list[dict[str, Any]] = resp.json()["sentences"]
    return out


async def test_an_unsourced_sentence_is_kept_so_the_choice_about_it_can_be_made_and_remembered(
    client_for: ClientFor, world: World, model: Callable[..., Scripted]
) -> None:
    model()
    first, second = await check(client_for, world)
    assert first["note_id"] is None and second["note_id"] and second["choice"] is None
    listed = (await (await client_for(world.expert)).get(world.url("trial-pack/notes"))).json()["notes"]
    assert [(n["id"], n["text"], n["choice"]) for n in listed] == [
        (second["note_id"], "Zz the patient later moved abroad.", None)
    ]
    chosen = await (await client_for(world.expert)).put(
        world.url(f"trial-pack/notes/{second['note_id']}"), json={"choice": "tell"}
    )
    assert chosen.status_code == 200 and chosen.json()["choice"] == "tell" and chosen.json()["decided_at"]
    again = await check(client_for, world)
    assert again[1]["note_id"] == second["note_id"] and again[1]["choice"] == "tell"
    left = await (await client_for(world.expert)).put(
        world.url(f"trial-pack/notes/{second['note_id']}"), json={"choice": "leave_out"}
    )
    assert left.json()["choice"] == "leave_out"
    listed = (await (await client_for(world.expert)).get(world.url("trial-pack/notes"))).json()["notes"]
    assert len(listed) == 1 and listed[0]["choice"] == "leave_out"


async def test_the_choice_about_a_note_is_the_named_expert_s_and_takes_known_choices_only(
    client_for: ClientFor, world: World, model: Callable[..., Scripted]
) -> None:
    model()
    _, second = await check(client_for, world)
    url = world.url(f"trial-pack/notes/{second['note_id']}")
    assert (await (await client_for(world.viewer)).put(url, json={"choice": "tell"})).status_code == 403
    assert (await (await client_for(world.outsider)).put(url, json={"choice": "tell"})).status_code == 404
    assert (await (await client_for(world.expert)).put(url, json={"choice": "shout"})).status_code == 422
    other = world.url("trial-pack/notes/00000000-0000-4000-8000-000000000000")
    assert (await (await client_for(world.expert)).put(other, json={"choice": "tell"})).status_code == 404
    assert (await (await client_for(world.viewer)).get(world.url("trial-pack/notes"))).status_code == 200
    assert (await (await client_for(world.outsider)).get(world.url("trial-pack/notes"))).status_code == 404


# --- telling about missing pages ------------------------------------------------------------------------------


async def test_telling_about_missing_pages_is_remembered_on_the_bundle(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    client = await client_for(world.expert)
    assert (await client.get(world.url("concordance"))).json()["bundle"]["missing_told_at"] is None
    assert (await client.post(world.url("concordance/tell-instructing-party"))).status_code == 200
    assert (await client.get(world.url("concordance"))).json()["bundle"]["missing_told_at"]


# --- exact page counts ---------------------------------------------------------------------------------------


async def test_the_built_pack_records_the_pages_each_section_took_and_the_preview_agrees(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    await prepared(client_for, worker_db, world, model)
    sections = [*ALL, "readiness"]
    assert (await settings(client_for, world, sections=sections)).status_code == 200
    latest = await built(client_for, worker_db, world)
    pack = latest["pack"]
    counted = {s["key"]: s["pages"] for s in pack["section_pages"]}
    assert set(counted) == set(sections) and all(n >= 1 for n in counted.values())
    assert sum(counted.values()) == pack["page_count"]
    preview = (await (await client_for(world.expert)).get(world.url("trial-pack/preview"))).json()
    assert {s["key"]: s["pages"] for s in preview["sections"] if s["key"] in counted} == counted
    assert pack["created_at"]
