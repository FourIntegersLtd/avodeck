"""Gentle warnings on the opinion, found again on every save (slice E brief; screen 15).

Each save runs the pattern check (`lint_regex`) on the saved text and returns the open warnings with the
opinion: the sentence, the kind and the words flagged, never other words (rule 1). The warnings from the
previous save are replaced; the words are stored encrypted under the case key with the opinion version
they were found in. Runs under the launch and the fixture packs (the loaded words are the pack's).
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.packs import default_library
from tests.fixtures.write.world import PLAN, Reads, World, app_for, build
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


def loaded(combo: Combo) -> str:
    return default_library().combine(*combo.codes).loaded_language()[0]


async def save(client: Any, w: World, etag: str, body: str) -> Any:
    refs = (
        [{"fact_id": str(w.verified), "page_id": str(w.read_page), "quote_verbatim": PLAN}]
        if "[[cite:1]]" in body
        else []
    )
    payload = {"body": body, "citation_refs": refs, "view": None, "provisional": False}
    saved = await client.put(w.url(f"questions/{w.question}/opinion"), json=payload, headers={"If-Match": etag})
    assert saved.status_code == 200, saved.text
    return saved.json()


def shown(out: dict[str, Any]) -> list[tuple[int, str, str, str, str]]:
    return [(i["sentence_index"], i["type"], i["words"], i["source"], i["status"]) for i in out["lint"]]


async def test_a_save_returns_the_warnings_in_the_saved_text(client_for: ClientFor, world: World, combo: Combo) -> None:
    client = await client_for(world.expert)
    out = await save(
        client, world, '"0"', f"The plan [[cite:1]] was made. Review was due in 3 weeks. It was {loaded(combo)}."
    )
    assert shown(out) == [
        (1, "uncited_fact", "3", "regex", "open"),
        (2, "loaded_language", loaded(combo), "regex", "open"),
    ]
    assert all(
        set(i) == {"id", "sentence_index", "sentence_start", "sentence_end", "type", "words", "source", "status"}
        for i in out["lint"]
    )


async def test_every_save_finds_the_warnings_again(
    client_for: ClientFor, world: World, admin: psycopg.Connection, combo: Combo
) -> None:
    client = await client_for(world.expert)
    await save(client, world, '"0"', f"Review was due in 3 weeks. It was {loaded(combo)}.")
    out = await save(client, world, '"1"', "Review was due in 3 weeks [[cite:1]]. It was late.")
    assert shown(out) == []
    out = await save(client, world, '"2"', f"It was {loaded(combo)}.")
    assert shown(out) == [(0, "loaded_language", loaded(combo), "regex", "open")]
    stored = admin.execute(
        "select opinion_version, source, status, words from app.lint_issues where case_id = %s", (world.case,)
    ).fetchall()
    assert [(r[0], r[1], r[2]) for r in stored] == [(3, "regex", "open")]
    assert loaded(combo).encode() not in bytes(stored[0][3])  # sealed under the case key


async def test_a_clean_text_has_no_warnings(client_for: ClientFor, world: World) -> None:
    client = await client_for(world.expert)
    assert shown(await save(client, world, '"0"', "My view is that the plan [[cite:1]] was reasonable.")) == []


async def test_the_lint_state_names_the_open_warnings_and_which_checks_passed(
    client_for: ClientFor, world: World, combo: Combo
) -> None:
    client = await client_for(world.expert)
    path = world.url(f"questions/{world.question}/opinion/lint")
    await save(client, world, '"0"', "My view is that the plan [[cite:1]] was reasonable.")
    clean = await client.get(path)
    assert clean.status_code == 200 and clean.json()["open_issues"] == []
    assert clean.json()["checks"] == [
        {"type": t, "passed": True} for t in ("uncited_fact", "loaded_language", "outside_expertise")
    ]
    out = await save(client, world, '"1"', f"Review was due in 3 weeks. It was {loaded(combo)}.")
    assert out["checks"] == [
        {"type": "uncited_fact", "passed": False},
        {"type": "loaded_language", "passed": False},
        {"type": "outside_expertise", "passed": True},
    ]
    state = (await client.get(path)).json()
    assert state["checks"] == out["checks"]
    assert [i["type"] for i in state["open_issues"]] == ["uncited_fact", "loaded_language"]
    assert state["issues"] == state["open_issues"]
    outsider = await client_for(world.outsider)
    assert (await outsider.get(path)).status_code == 404


async def test_the_workspace_carries_the_checks_with_or_without_an_opinion(
    client_for: ClientFor, world: World
) -> None:
    client = await client_for(world.expert)
    path = world.url(f"questions/{world.question}/workspace")
    first = (await client.get(path)).json()
    assert [c["passed"] for c in first["checks"]] == [True, True, True]
    await save(client, world, '"0"', "Review was due in 3 weeks.")
    later = (await client.get(path)).json()
    assert [(c["type"], c["passed"]) for c in later["checks"]][0] == ("uncited_fact", False)
    assert later["opinion"]["checks"] == later["checks"]
