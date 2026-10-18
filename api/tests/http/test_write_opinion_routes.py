"""Screen 15: the expert's opinion on a question (architecture 5.2, slice E brief, scenario S9).

`PUT .../questions/{q_id}/opinion` saves the expert's text, its citation objects, their view (a code
from the case-type pack) and the provisional flag. It takes If-Match with the last ETag (`"0"` before
the first save): a save from a window that has fallen behind gets 412 `stale` with both versions, so
the screen can ask which to keep. Every save keeps a version; restoring one adds a new version and
never changes the old. Citations follow the citation rule (422 otherwise). Only the named expert
writes (403 for the other case roles); everyone on the case can read the versions; anyone else
gets 404. Runs under the launch and the fixture packs (rule 11).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from tests.fixtures.write.world import PLAN, RESULT, Reads, World, app_for, build
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


def views(combo: Combo) -> list[str]:
    opinion = combo.raw("case-type", "opinion.yaml")
    return [str(v["code"]) for v in opinion["opinion_views"]]


def cite(fact: uuid.UUID, page: uuid.UUID, quote: str) -> dict[str, str]:
    return {"fact_id": str(fact), "page_id": str(page), "quote_verbatim": quote}


def opinion(
    w: World, body: str = "My view [[cite:1]].", refs: list[dict[str, str]] | None = None, **more: Any
) -> dict[str, Any]:
    return {
        "body": body,
        "citation_refs": [cite(w.verified, w.read_page, PLAN)] if refs is None else refs,
        "view": None,
        "provisional": False,
        **more,
    }


async def save(client: Any, w: World, etag: str, payload: dict[str, Any]) -> Any:
    return await client.put(w.url(f"questions/{w.question}/opinion"), json=payload, headers={"If-Match": etag})


async def test_the_first_save_needs_if_match_0_and_returns_the_opinion_with_its_etag(
    client_for: ClientFor, world: World, combo: Combo
) -> None:
    client = await client_for(world.expert)
    path = world.url(f"questions/{world.question}/opinion")
    assert (await client.put(path, json=opinion(world))).status_code == 428
    view = views(combo)[0]
    saved = await save(client, world, '"0"', opinion(world, view=view, provisional=True))
    assert saved.status_code == 200, saved.text
    assert saved.headers["etag"] == '"1"'
    out = saved.json()
    assert out["question_id"] == str(world.question)
    assert (out["body"], out["view"], out["provisional"], out["version"]) == ("My view [[cite:1]].", view, True, 1)
    assert out["citation_refs"] == [cite(world.verified, world.read_page, PLAN)]


async def test_a_valid_citation_is_stored_and_the_marker_round_trips(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.expert)
    refs = [cite(world.verified, world.read_page, "Review 3/52"), cite(world.result, world.read_page, "ferritin 9")]
    body = "The plan [[cite:1]] and the result [[cite:2]], again [[cite:1]]."
    saved = await save(client, world, '"0"', opinion(world, body, refs))
    assert saved.status_code == 200, saved.text
    versions = await client.get(world.url(f"opinions/{saved.json()['id']}/versions"))
    assert versions.status_code == 200
    [only] = versions.json()["versions"]
    assert (only["body"], only["citation_refs"]) == (body, refs)


@pytest.mark.parametrize("which", ["suggested", "rejected", "on_unread_page"])
async def test_citing_a_fact_that_is_not_citable_is_refused(client_for: ClientFor, world: World, which: str) -> None:
    client = await client_for(world.expert)
    page = world.unread_page if which == "on_unread_page" else world.read_page
    refused = await save(client, world, '"0"', opinion(world, refs=[cite(getattr(world, which), page, PLAN)]))
    assert refused.status_code == 422
    assert refused.json()["error"] == {
        "code": "invalid",
        "message": refused.json()["error"]["message"],
        "citation": 1,
        "reason": "not_citable",
    }


async def test_citing_words_that_are_not_in_the_fact_is_refused_and_nothing_is_saved(
    client_for: ClientFor, world: World
) -> None:
    client = await client_for(world.expert)
    refused = await save(client, world, '"0"', opinion(world, refs=[cite(world.verified, world.read_page, RESULT)]))
    assert refused.status_code == 422
    assert refused.json()["error"]["reason"] == "quote_not_in_fact"
    assert RESULT not in refused.text
    assert (await save(client, world, '"0"', opinion(world))).status_code == 200


async def test_a_view_that_is_not_in_the_pack_is_refused(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.expert)
    assert (await save(client, world, '"0"', opinion(world, view="not_a_view"))).status_code == 422


async def test_a_save_from_a_window_that_has_fallen_behind_gets_412_with_both_versions(
    client_for: ClientFor, world: World
) -> None:
    first, second = await client_for(world.expert), await client_for(world.expert)
    assert (await save(first, world, '"0"', opinion(world, "Window one [[cite:1]]."))).status_code == 200
    assert (await save(first, world, '"1"', opinion(world, "Window one again [[cite:1]]."))).status_code == 200
    late = await save(second, world, '"1"', opinion(world, "Window two [[cite:1]]."))
    assert late.status_code == 412
    error = late.json()["error"]
    assert error["code"] == "stale"
    assert error["yours"]["body"] == "Window two [[cite:1]]."
    assert (error["saved"]["body"], error["saved"]["version"]) == ("Window one again [[cite:1]].", 2)
    kept = await save(second, world, '"2"', opinion(world, "Window two [[cite:1]]."))
    assert kept.status_code == 200 and kept.json()["version"] == 3


async def test_every_save_keeps_a_version_and_restoring_adds_a_new_one(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.expert)
    for n, words in enumerate(["First [[cite:1]].", "Second [[cite:1]].", "Third [[cite:1]]."]):
        saved = await save(client, world, f'"{n}"', opinion(world, words))
        assert saved.status_code == 200
    opinion_id = saved.json()["id"]
    listed = (await client.get(world.url(f"opinions/{opinion_id}/versions"))).json()["versions"]
    assert [(v["version_no"], v["body"]) for v in listed] == [
        (3, "Third [[cite:1]]."),
        (2, "Second [[cite:1]]."),
        (1, "First [[cite:1]]."),
    ]
    restore = world.url(f"opinions/{opinion_id}/versions/1/restore")
    assert (await client.post(restore, headers={"If-Match": '"2"'})).status_code == 412
    restored = await client.post(restore, headers={"If-Match": '"3"'})
    assert restored.status_code == 200, restored.text
    assert (restored.json()["version"], restored.json()["body"]) == (4, "First [[cite:1]].")
    assert restored.headers["etag"] == '"4"'
    after = (await client.get(world.url(f"opinions/{opinion_id}/versions"))).json()["versions"]
    assert [(v["version_no"], v["body"], v["restored_from"]) for v in after][:2] == [
        (4, "First [[cite:1]].", 1),
        (3, "Third [[cite:1]].", None),
    ]
    assert len(after) == 4
    missing = world.url(f"opinions/{opinion_id}/versions/9/restore")
    assert (await client.post(missing, headers={"If-Match": '"4"'})).status_code == 404


async def test_only_the_named_expert_writes_an_opinion(client_for: ClientFor, world: World) -> None:
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer):
        client = await client_for(person)
        assert (await save(client, world, '"0"', opinion(world))).status_code == 403
    outsider = await client_for(world.outsider)
    assert (await save(outsider, world, '"0"', opinion(world))).status_code == 404


async def test_everyone_on_the_case_reads_the_versions_and_others_get_404(client_for: ClientFor, world: World) -> None:
    expert = await client_for(world.expert)
    opinion_id = (await save(expert, world, '"0"', opinion(world))).json()["id"]
    path = world.url(f"opinions/{opinion_id}/versions")
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer):
        assert (await (await client_for(person)).get(path)).status_code == 200
    assert (await (await client_for(world.outsider)).get(path)).status_code == 404
    assert (await expert.get(world.url(f"opinions/{uuid.uuid4()}/versions"))).status_code == 404


async def test_an_unknown_or_unconfirmed_question_is_404(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    from tests.support.contracts import add_question

    client = await client_for(world.expert)
    unconfirmed = add_question(admin, world.case, number=2, status="suggested")
    for question in (uuid.uuid4(), unconfirmed):
        path = world.url(f"questions/{question}/opinion")
        assert (await client.put(path, json=opinion(world), headers={"If-Match": '"0"'})).status_code == 404


async def test_the_opinion_text_is_stored_encrypted(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    client = await client_for(world.expert)
    assert (await save(client, world, '"0"', opinion(world, "Plainly my words [[cite:1]]."))).status_code == 200
    row = admin.execute("select body from app.opinions where question_id = %s", (world.question,)).fetchone()
    assert row is not None and b"Plainly" not in bytes(row[0])
