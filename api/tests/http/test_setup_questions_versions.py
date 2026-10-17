"""Screen 06 across several sets of instructions (slice C review findings 3, 4 and 5).

- Every letter is read, even when a second one arrives before the first has been read.
- Oral instructions carry no questions: they never reopen a confirmed set, and they do not hide a lock
  from the next letter (which reopens the set when it adds a question).
- A letter recorded without its file reopens the set, and the screen says so.
- When reading the letter fails, the screen says so (not "still being read" forever), confirm-all says
  why, and the expert or an assistant can ask for it to be read again.
Nothing from the letter is printed (rule 6).
"""

# ruff: noqa: F811  (the shared fixtures are imported from the questions tests)
from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.ai.bedrock import RecordedBedrock
from app.core.db import Database
from tests.http.test_setup_questions_routes import (  # noqa: F401
    PARTY,
    PARTY_ROLE,
    QUESTIONS,
    SECOND,
    SUPPLEMENTARY,
    add_letter,
    app,
    crypto,
    model,
    read_letter,
    run_job,
    storage,
    url,
    worker_db,
    world,
)
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio

ORAL = {"received_on": "2024-03-05", "text": "Please also look at the March visit."}


async def test_two_letters_queued_together_are_both_read(
    client_for: ClientFor,
    world: dict[str, uuid.UUID],
    worker_db: Database,
    model: RecordedBedrock,
    admin: psycopg.Connection,
) -> None:
    client = await client_for(world["expert"])
    assert (await client.post(url(world, "parties"), json={"role_key": PARTY_ROLE, "name": PARTY})).status_code == 201
    first = await add_letter(client, world)
    second = await add_letter(client, world, SUPPLEMENTARY, "supplementary")
    await run_job(worker_db, world, first["job_id"])
    await run_job(worker_db, world, second["job_id"])

    read = admin.execute(
        "select instruction_version_id from app.instruction_extractions where case_id = %s", (world["case"],)
    ).fetchall()
    assert {r[0] for r in read} == {uuid.UUID(first["id"]), uuid.UUID(second["id"])}
    got = (await client.get(url(world, "questions"))).json()
    assert got["reading"] is False
    texts = [q["text_verbatim"] for q in got["questions"]]
    assert texts == [q["text_verbatim"] for q in QUESTIONS["questions"]] + [SECOND["new_question"]]


async def test_oral_instructions_keep_a_confirmed_set_locked(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    await read_letter(client, world, worker_db)
    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 200
    assert (await client.put(url(world, "oral-instructions"), json=ORAL)).status_code == 200

    got = (await client.get(url(world, "questions"))).json()
    assert got["locked"] is True and got["reading"] is False
    assert (await client.get(url(world, "probe-questions-locked"))).status_code == 200


async def test_an_oral_instruction_does_not_hide_the_lock_from_the_next_letter(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    await read_letter(client, world, worker_db)
    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 200
    assert (await client.put(url(world, "oral-instructions"), json=ORAL)).status_code == 200

    added = await add_letter(client, world, SUPPLEMENTARY, "supplementary")
    assert added["reopens_questions"] is True
    await run_job(worker_db, world, added["job_id"])
    got = (await client.get(url(world, "questions"))).json()
    assert got["locked"] is False and got["reopened"] is True
    assert "setup.questions.warning.reopened" in got["warnings"]
    assert {q["status"] for q in got["questions"]} == {"suggested"}


async def test_a_letter_without_its_file_reopens_the_set_and_says_so(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    await read_letter(client, world, worker_db)
    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 200
    added = await client.post(url(world, "instructions"), json={"kind": "supplementary"})
    assert added.status_code == 201 and added.json()["reopens_questions"] is True

    got = (await client.get(url(world, "questions"))).json()
    assert got["locked"] is False and got["reading"] is False
    assert "setup.questions.warning.letter_without_file" in got["warnings"]


async def test_a_failed_reading_is_shown_and_can_be_tried_again(
    client_for: ClientFor,
    world: dict[str, uuid.UUID],
    worker_db: Database,
    model: RecordedBedrock,
    admin: psycopg.Connection,
) -> None:
    client = await client_for(world["expert"])
    assert (await client.post(url(world, "parties"), json={"role_key": PARTY_ROLE, "name": PARTY})).status_code == 201
    added = await add_letter(client, world)
    # the job's attempts are spent (the guard allows only queued -> running -> failed)
    for status in ("running", "failed"):
        admin.execute(
            "update app.jobs set status = %s, error_code = 'job_error' where id = %s", (status, added["job_id"])
        )

    got = (await client.get(url(world, "questions"))).json()
    assert got["reading"] is False and got["read_failed"] is True
    assert "setup.questions.warning.read_failed" in got["warnings"]
    refused = await client.post(url(world, "questions/confirm-all"))
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "letter_not_read"

    assert (await (await client_for(world["viewer"])).post(url(world, "questions/read-again"))).status_code == 403
    assert (await (await client_for(world["outsider"])).post(url(world, "questions/read-again"))).status_code == 404
    again = await (await client_for(world["assistant"])).post(url(world, "questions/read-again"))
    assert again.status_code == 202, again.status_code
    job_id = again.json()["job_id"]
    got = (await client.get(url(world, "questions"))).json()
    assert got["reading"] is True and got["read_failed"] is False

    await run_job(worker_db, world, job_id)
    got = (await client.get(url(world, "questions"))).json()
    assert got["reading"] is False and got["read_failed"] is False and len(got["questions"]) == 4
    assert (await client.post(url(world, "questions/read-again"))).status_code == 409  # nothing left to read
