"""Slice B, screen S10 (pages we couldn't read), rotation and the reader's Today items (architecture 5, 09 Reader).

- `GET /v1/cases/{id}/pages/unreadable`: the unreadable pages (quarantined pages left out) in page order,
  each with whether it is done (typed out, or a clearer copy asked for), plus the done count.
- `PUT /v1/cases/{id}/pages/{page_no}/transcription` `{text, relevance?}`: stores the expert's text,
  counts as reading the page, and queues re-extraction through `core/contracts/pipeline.py`
  `extract_page_text` (slice A tokenises the text into `source = 'expert'` words). 202 `{job_id}`; 503
  and nothing saved while nothing implements the entry point.
- `PATCH /v1/cases/{id}/pages/{page_no}` `{relevance?, needs_clearer_copy?}`.
- `POST /v1/cases/{id}/pages/{page_id}/rotate` `{degrees: 90|180|270}`: turns the page clockwise, keeps its
  number and re-reads it through `process_pages`. 202 `{job_id}`.
- Today: `reader_pages_to_read` (S09) and `reader_pages_to_type` (S10) for the named expert, with counts.
- None of the expert's text reaches a log line or a job row (rule 6).
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.contracts.pipeline import (
    extract_page_text_entry,
    process_pages,
    process_pages_entry,
    transcription_purpose,
)
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
EXPECTED = Path(__file__).parents[3] / "evals/gb-eaw/clin-neg-breach/general-practice/expected/unreadable.json"
UNREADABLE: list[int] = json.loads(EXPECTED.read_text())
TEXT = "Seen today Zebedee Quillfeather chest clear review in two weeks"


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    from tests.support.files import build_app

    return build_app(api_db, auth_env, crypto=CRYPTO)


@pytest.fixture
def calls() -> Iterator[list[tuple[str, uuid.UUID, Any]]]:
    """Stand-ins for slice A's entry points that record what they were asked to do."""
    seen: list[tuple[str, uuid.UUID, Any]] = []

    async def extract(conn: psycopg.AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> uuid.UUID:
        seen.append(("extract", case_id, page_id))
        return uuid.uuid4()

    async def pages(conn: psycopg.AsyncConnection, case_id: uuid.UUID, page_ids: Any) -> uuid.UUID:
        seen.append(("pages", case_id, list(page_ids)))
        return uuid.uuid4()

    with extract_page_text_entry.override(extract), process_pages_entry.override(pages):
        yield seen


@pytest.fixture
async def world(seed: Seed, admin: psycopg.Connection, client_for: Any) -> dict[str, Any]:
    expert, assistant, stranger = seed.person(), seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case_member(case, assistant, "assistant", added_by=expert)
    doc = seed.document(case)
    pages = {n: seed.page(case, doc, n) for n in [1, 2, *UNREADABLE]}
    admin.execute(
        "update app.pages set unreadable = true, handwriting = true where id = any(%s)",
        ([pages[n] for n in UNREADABLE],),
    )
    quarantined = seed.page(case, doc, 999)
    admin.execute("update app.pages set unreadable = true, quarantined = true where id = %s", (quarantined,))
    return {
        "case": case,
        "expert": expert,
        "assistant": assistant,
        "pages": pages,
        "client": await client_for(expert),
        "stranger": await client_for(stranger),
    }


def url(w: dict[str, Any], tail: str) -> str:
    return f"/v1/cases/{w['case']}/{tail}"


async def queue(client: Any, w: dict[str, Any]) -> dict[str, Any]:
    response = await client.get(url(w, "pages/unreadable"))
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def page_row(admin: psycopg.Connection, page_id: uuid.UUID) -> tuple[Any, ...]:
    row = admin.execute(
        "select transcribed_by_expert_text, relevance, needs_clearer_copy, rotation, bundle_page_no"
        " from app.pages where id = %s",
        (page_id,),
    ).fetchone()
    assert row is not None
    return row


async def test_the_queue_lists_the_unreadable_pages_with_a_done_count(world: dict[str, Any], calls: list[Any]) -> None:
    client, first, second = world["client"], UNREADABLE[0], UNREADABLE[1]
    listed = await queue(client, world)
    assert [p["page_no"] for p in listed["pages"]] == UNREADABLE
    assert (listed["done"], listed["total"]) == (0, len(UNREADABLE))
    assert not any(p["done"] for p in listed["pages"])

    assert (await client.put(url(world, f"pages/{first}/transcription"), json={"text": TEXT})).status_code == 202
    assert (await client.patch(url(world, f"pages/{second}"), json={"needs_clearer_copy": True})).status_code == 200

    listed = await queue(client, world)
    assert listed["done"] == 2
    shown = {p["page_no"]: p for p in listed["pages"]}
    assert shown[first]["done"] and shown[first]["has_transcription"]
    assert shown[second]["done"] and shown[second]["needs_clearer_copy"]
    assert (await world["stranger"].get(url(world, "pages/unreadable"))).status_code == 404


async def test_saving_a_transcription_queues_re_extraction_and_counts_as_read(
    admin: psycopg.Connection, api_db: Database, world: dict[str, Any], calls: list[Any]
) -> None:
    client, n = world["client"], UNREADABLE[0]
    page_id = world["pages"][n]
    assert (await client.get(url(world, f"pages/{n}/facts"))).status_code == 423

    saved = await client.put(url(world, f"pages/{n}/transcription"), json={"text": TEXT, "relevance": "relevant"})
    assert saved.status_code == 202, saved.text
    assert uuid.UUID(saved.json()["job_id"])
    assert calls == [("extract", world["case"], page_id)]
    sealed, relevance = page_row(admin, page_id)[:2]
    assert relevance == "relevant"
    assert isinstance(sealed, bytes) and b"chest" not in sealed, "the typed text is sealed with the case key"
    from tests.support.db import scope_for

    async with api_db.request(scope_for(world["expert"])) as conn:
        opened = await CRYPTO.decrypt(conn, world["case"], sealed, purpose=transcription_purpose(page_id))
    assert opened.decode() == TEXT
    assert admin.execute("select person_id from app.page_reads where page_id = %s", (page_id,)).fetchall() == [
        (world["expert"],)
    ]
    assert (await client.get(url(world, f"pages/{n}/facts"))).status_code == 200

    for bad in ({"text": ""}, {"text": TEXT, "relevance": "maybe"}):
        assert (await client.put(url(world, f"pages/{n}/transcription"), json=bad)).status_code == 422
    assert (await client.put(url(world, "pages/5000/transcription"), json={"text": TEXT})).status_code == 404
    assert (await world["stranger"].put(url(world, f"pages/{n}/transcription"), json={"text": TEXT})).status_code == 404


async def test_without_re_extraction_nothing_is_saved(admin: psycopg.Connection, world: dict[str, Any]) -> None:
    n = UNREADABLE[0]
    with extract_page_text_entry.override(None):  # type: ignore[arg-type]
        response = await world["client"].put(url(world, f"pages/{n}/transcription"), json={"text": TEXT})
    assert response.status_code == 503
    assert page_row(admin, world["pages"][n])[0] is None
    assert admin.execute("select count(*) from app.page_reads where case_id = %s", (world["case"],)).fetchone() == (0,)


async def test_patch_sets_relevance_and_asks_for_a_clearer_copy(
    admin: psycopg.Connection, client_for: Any, world: dict[str, Any]
) -> None:
    n = UNREADABLE[2]
    response = await world["client"].patch(
        url(world, f"pages/{n}"), json={"relevance": "background", "needs_clearer_copy": True}
    )
    assert response.status_code == 200, response.text
    assert (response.json()["relevance"], response.json()["needs_clearer_copy"]) == ("background", True)
    assert page_row(admin, world["pages"][n])[1:3] == ("background", True)

    assistant = await client_for(world["assistant"])
    undone = await assistant.patch(url(world, f"pages/{n}"), json={"needs_clearer_copy": False})
    assert undone.status_code == 200 and undone.json()["relevance"] == "background"
    assert page_row(admin, world["pages"][n])[2] is False

    for bad in ({}, {"relevance": "maybe"}):
        assert (await world["client"].patch(url(world, f"pages/{n}"), json=bad)).status_code == 422
    assert (await world["stranger"].patch(url(world, f"pages/{n}"), json={"relevance": "relevant"})).status_code == 404


async def test_rotating_turns_the_page_keeps_its_number_and_reads_it_again(
    admin: psycopg.Connection, world: dict[str, Any], calls: list[Any]
) -> None:
    page_id = world["pages"][1]
    rotate = url(world, f"pages/{page_id}/rotate")

    first = await world["client"].post(rotate, json={"degrees": 90})
    assert first.status_code == 202, first.text
    assert uuid.UUID(first.json()["job_id"])
    assert page_row(admin, page_id)[3:] == (90, 1)
    assert calls == [("pages", world["case"], [page_id])]

    assert (await world["client"].post(rotate, json={"degrees": 270})).status_code == 202
    assert page_row(admin, page_id)[3] == 0
    assert (await world["client"].post(rotate, json={"degrees": 45})).status_code == 422
    assert (
        await world["client"].post(url(world, f"pages/{uuid.uuid4()}/rotate"), json={"degrees": 90})
    ).status_code == 404
    assert (await world["stranger"].post(rotate, json={"degrees": 90})).status_code == 404

    with process_pages_entry.override(None):  # type: ignore[arg-type]
        assert (await world["client"].post(rotate, json={"degrees": 180})).status_code == 503
    assert page_row(admin, page_id)[3] == 0


async def today(client: Any, kind: str) -> list[dict[str, Any]]:
    response = await client.get("/v1/today")
    assert response.status_code == 200, response.text
    return [i for i in response.json()["needs_you"] if i["kind"] == kind]


async def test_today_shows_pages_to_read_and_pages_to_type(
    client_for: Any, world: dict[str, Any], calls: list[Any]
) -> None:
    client, total = world["client"], 2 + len(UNREADABLE)
    [to_read] = await today(client, "reader_pages_to_read")
    assert (to_read["screen"], to_read["count"], to_read["case_id"]) == ("S09", total, str(world["case"]))
    assert to_read["minutes"] and to_read["minutes"] > 0
    [to_type] = await today(client, "reader_pages_to_type")
    assert (to_type["screen"], to_type["count"]) == ("S10", len(UNREADABLE))
    assert all(i["label_key"].startswith("reader.") for i in (to_read, to_type))

    assert (await client.post(url(world, "pages/1/read"), json={"dwell_seconds": 20})).status_code == 200
    assert (
        await client.put(url(world, f"pages/{UNREADABLE[0]}/transcription"), json={"text": TEXT})
    ).status_code == 202
    assert [i["count"] for i in await today(client, "reader_pages_to_read")] == [total - 2]
    assert [i["count"] for i in await today(client, "reader_pages_to_type")] == [len(UNREADABLE) - 1]
    assistant = await client_for(world["assistant"])
    assert await today(assistant, "reader_pages_to_read") == []


async def test_the_experts_text_reaches_no_log_or_job_row(
    admin: psycopg.Connection, caplog: pytest.LogCaptureFixture, world: dict[str, Any]
) -> None:
    async def extract(conn: psycopg.AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> uuid.UUID:
        return await process_pages(conn, case_id, [page_id])

    caplog.set_level(logging.DEBUG)
    with extract_page_text_entry.override(extract):
        saved = await world["client"].put(url(world, f"pages/{UNREADABLE[0]}/transcription"), json={"text": TEXT})
    assert saved.status_code == 202, saved.text
    jobs = admin.execute("select row_to_json(j)::text from app.jobs j where case_id = %s", (world["case"],)).fetchall()
    assert jobs, "the re-extraction job was queued"
    for word in ("Zebedee", "Quillfeather", "chest clear"):
        assert word not in caplog.text
        assert not any(word in row[0] for row in jobs)


async def test_a_page_with_nothing_relevant_is_done_and_leaves_the_pages_to_type(
    client_for: Any, world: dict[str, Any], admin: psycopg.Connection, calls: list[Any]
) -> None:
    client, n = world["client"], UNREADABLE[0]
    response = await client.patch(url(world, f"pages/{n}"), json={"relevance": "nothing"})
    assert response.status_code == 200 and response.json()["relevance"] == "nothing"
    shown = {p["page_no"]: p for p in (await queue(client, world))["pages"]}
    assert shown[n]["done"] and not shown[n]["has_transcription"]
    assert [i["count"] for i in await today(client, "reader_pages_to_type")] == [len(UNREADABLE) - 1]
    assistant = await client_for(world["assistant"])
    assert (await assistant.patch(url(world, f"pages/{n}"), json={"relevance": "relevant"})).status_code == 200


async def test_a_page_set_aside_as_a_copy_or_given_a_clearer_copy_is_done(
    seed: Seed, admin: psycopg.Connection, world: dict[str, Any], calls: list[Any]
) -> None:
    client, copy_no, replaced_no = world["client"], UNREADABLE[0], UNREADABLE[1]
    admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (world["pages"][1], world["pages"][copy_no]))
    later = seed.document(world["case"])
    admin.execute(
        "insert into app.page_replacements (case_id, page_id, document_id, requested_by) values (%s, %s, %s, %s)",
        (world["case"], world["pages"][replaced_no], later, world["expert"]),
    )
    shown = {p["page_no"]: p for p in (await queue(client, world))["pages"]}
    assert shown[copy_no]["done"] and shown[copy_no]["is_copy"]
    assert shown[replaced_no]["done"] and shown[replaced_no]["has_clearer_copy"]
    assert not shown[UNREADABLE[2]]["done"]
