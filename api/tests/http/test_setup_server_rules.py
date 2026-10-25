"""Rule 4: the server decides which records count, their order and the counts; the screen only renders.

Questions `answerable`, `confirmed_count`/`total`, `letter` (cited paragraphs in letter order), instructions
`letters_only` and prefill `open`.
"""

# ruff: noqa: F811  (the shared fixtures are imported from the questions tests)
from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from app.core.ai.bedrock import RecordedBedrock
from app.core.db import Database
from tests.http.test_setup_prefill_routes import prefilled
from tests.http.test_setup_questions_routes import (  # noqa: F401
    add_letter,
    app,
    crypto,
    model,
    read_letter,
    storage,
    url,
    worker_db,
    world,
)
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def move(client: Any, world: dict[str, uuid.UUID], kind: str, item: dict[str, Any], body: dict[str, Any]) -> Any:
    done = await client.patch(
        url(world, f"{kind}/{item['id']}"), json=body, headers={"if-match": f'"{item["version"]}"'}
    )
    assert done.status_code == 200, done.status_code
    return done


async def test_answerable_questions_are_the_confirmed_or_edited_ones_in_number_order(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    qs = got["questions"]
    assert len(qs) >= 3
    none = await client.get(url(world, "questions"), params={"answerable": "true"})
    assert none.status_code == 200 and none.json()["questions"] == []
    await move(client, world, "questions", qs[2], {"confirm": True})
    await move(client, world, "questions", qs[0], {"text": "My own wording."})
    every = (await client.get(url(world, "questions"))).json()["questions"]
    assert len(every) == len(qs)
    only = (await client.get(url(world, "questions"), params={"answerable": "true"})).json()
    assert [q["number"] for q in only["questions"]] == [qs[0]["number"], qs[2]["number"]]
    assert {q["status"] for q in only["questions"]} <= {"confirmed", "edited"}
    off = (await client.get(url(world, "questions"), params={"answerable": "false"})).json()
    assert off["questions"] == every


async def test_the_reply_carries_the_done_count_and_total(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    assert got["total"] == len(got["questions"]) + len(got["assumptions"])
    assert got["confirmed_count"] == 0
    await move(client, world, "questions", got["questions"][0], {"confirm": True})
    await move(client, world, "assumptions", got["assumptions"][0], {"confirm": True})
    again = (await client.get(url(world, "questions"))).json()
    assert again["confirmed_count"] == 2 and again["total"] == got["total"]
    only = (await client.get(url(world, "questions"), params={"answerable": "true"})).json()
    assert only["confirmed_count"] == 2 and only["total"] == got["total"]


async def test_the_letter_list_is_cited_paragraphs_in_paragraph_order(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await read_letter(client, world, worker_db)
    cited = [i for i in got["questions"] + got["assumptions"] if i["para_ref"]]
    assert cited
    shown = got["letter"]
    assert {i["id"] for i in shown} == {i["id"] for i in cited}
    assert {i["kind"] for i in shown} <= {"question", "assumption"}

    def key(ref: str) -> list[Any]:
        return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", ref) if p]

    refs = [i["para_ref"] for i in shown]
    assert refs == sorted(refs, key=key)
    first = next(i for i in shown if i["kind"] == "question")
    source = next(q for q in got["questions"] if q["id"] == first["id"])
    assert first["text_verbatim"] == source["text_verbatim"]
    assert first["outside_expertise"] == source["outside_expertise"]


async def test_instructions_can_be_limited_to_the_letters_with_a_file(
    client_for: ClientFor, world: dict[str, uuid.UUID], model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    added = await add_letter(client, world)
    oral = await client.put(url(world, "oral-instructions"), json={"received_on": "2026-01-05", "text": "By phone."})
    assert oral.status_code == 200, oral.status_code
    every = (await client.get(url(world, "instructions"))).json()
    assert len(every) == 2
    letters = (await client.get(url(world, "instructions"), params={"letters_only": "true"})).json()
    assert [i["id"] for i in letters] == [added["id"]]
    assert all(i["document_id"] for i in letters)


async def test_prefill_can_return_only_the_open_suggestions(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await prefilled(client, world, worker_db)
    assert len(got["suggestions"]) >= 2
    first = got["suggestions"][0]
    decided = await client.patch(url(world, f"prefill/{first['id']}"), json={"decision": "dismissed"})
    assert decided.status_code == 200
    every = (await client.get(url(world, "prefill"))).json()
    assert len(every["suggestions"]) == len(got["suggestions"])
    open_ = (await client.get(url(world, "prefill"), params={"open": "true"})).json()
    waiting = [s["id"] for s in every["suggestions"] if s["status"] == "suggested"]
    assert [s["id"] for s in open_["suggestions"]] == waiting
    assert first["id"] not in waiting
    assert open_["reading"] == every["reading"] and open_["rejected"] == every["rejected"]
