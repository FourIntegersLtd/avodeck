"""Screen 04: details suggested from the letter (architecture 5.2 and 6.3 T-04, slice C brief).

`POST /cases/{id}/prefill` returns 202 and a job. The `prefill_case` job reads the latest letter
(recorded OCR), takes identifiers out (rule 7), asks T-04 through the AI kit and keeps a suggestion only
when it passes the word check (rule 2): a name must be exactly the cited words, a key must come from the
pack, a due date must be written out in the cited words. The recorded answer gives three honest
suggestions and one due date the letter never writes ("within 8 weeks"), which is refused. Nothing is
applied to the case: each suggestion waits for the expert to accept or dismiss it, and accepting only
records the choice so screen 04 can fill its form. Nothing from the letter is printed (rule 6).
"""

# ruff: noqa: F811  (the shared fixtures are imported from the questions tests)
from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from app.core.ai.bedrock import RecordedBedrock
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.setup.services import SetupServices, configure
from tests.http.test_setup_questions_routes import (  # noqa: F401
    IDENTIFIERS,
    PARTY,
    PARTY_ROLE,
    add_letter,
    app,
    crypto,
    model,
    storage,
    url,
    worker_db,
    world,
)
from tests.support.db import ClientFor
from tests.support.packs import LAUNCH

pytestmark = pytest.mark.anyio

IP_TYPE = LAUNCH.raw("case-type", "pack.yaml")["instructing_party_types"][0]
SUGGESTED = [
    ("instructing_party_name", "Harland & Price LLP"),
    ("instructing_party_type", IP_TYPE),
    ("party_role", PARTY_ROLE),
]


async def start(client: Any, world: dict[str, uuid.UUID]) -> str:
    started = await client.post(url(world, "prefill"))
    assert started.status_code == 202, started.status_code
    return str(started.json()["job_id"])


async def run(worker_db: Database, world: dict[str, uuid.UUID], job_id: str) -> None:
    from app.features.setup.jobs import prefill_case

    await prefill_case(JobContext(worker_db, uuid.UUID(job_id), world["case"], "prefill_case", 1))


async def prefilled(client: Any, world: dict[str, uuid.UUID], worker_db: Database) -> dict[str, Any]:
    assert (await client.post(url(world, "parties"), json={"role_key": PARTY_ROLE, "name": PARTY})).status_code == 201
    await add_letter(client, world)
    await run(worker_db, world, await start(client, world))
    got = await client.get(url(world, "prefill"))
    assert got.status_code == 200, got.status_code
    return dict(got.json())


async def test_prefill_needs_a_letter_and_the_right_role(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["expert"])
    assert (await client.post(url(world, "prefill"))).status_code == 409  # no letter yet
    await add_letter(client, world)
    assert (await (await client_for(world["viewer"])).post(url(world, "prefill"))).status_code == 403
    assert (await (await client_for(world["outsider"])).post(url(world, "prefill"))).status_code == 404
    assert (await (await client_for(world["outsider"])).get(url(world, "prefill"))).status_code == 404
    await start(await client_for(world["assistant"]), world)


async def test_suggestions_cite_the_letter_and_a_made_up_date_is_refused(
    client_for: ClientFor,
    world: dict[str, uuid.UUID],
    worker_db: Database,
    model: RecordedBedrock,
    admin: psycopg.Connection,
) -> None:
    client = await client_for(world["expert"])
    got = await prefilled(client, world, worker_db)
    assert [(s["field"], s["value"]) for s in got["suggestions"]] == SUGGESTED
    assert {s["status"] for s in got["suggestions"]} == {"suggested"}
    assert all(s["word_ids"] for s in got["suggestions"])
    assert got["rejected"] == 1 and got["reading"] is False

    assert len(model.requests) == 1
    assert not [i for i in IDENTIFIERS if i in model.requests[0].text], "an identifier reached the model"
    stored = admin.execute("select value from app.prefill_suggestions where case_id = %s", (world["case"],)).fetchall()
    assert stored and all(b"Harland" not in bytes(r[0]) for r in stored)

    # nothing is applied to the case by the job
    assert (await client.get(url(world, "instructing-parties"))).json()["current"] == []


async def test_running_again_does_not_repeat_suggestions(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    first = await prefilled(client, world, worker_db)
    accepted = first["suggestions"][0]["id"]
    assert (await client.patch(url(world, f"prefill/{accepted}"), json={"decision": "accepted"})).status_code == 200
    await run(worker_db, world, await start(client, world))
    again = (await client.get(url(world, "prefill"))).json()
    assert [(s["field"], s["value"]) for s in again["suggestions"]] == SUGGESTED
    assert [s["status"] for s in again["suggestions"]] == ["accepted", "suggested", "suggested"]


async def test_the_expert_accepts_or_dismisses_each_suggestion(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    got = await prefilled(client, world, worker_db)
    first, second = got["suggestions"][0]["id"], got["suggestions"][1]["id"]
    viewer = await client_for(world["viewer"])
    assert (await viewer.get(url(world, "prefill"))).status_code == 200
    assert (await viewer.patch(url(world, f"prefill/{first}"), json={"decision": "accepted"})).status_code == 403
    assert (await client.patch(url(world, f"prefill/{uuid.uuid4()}"), json={"decision": "accepted"})).status_code == 404
    assert (await client.patch(url(world, f"prefill/{first}"), json={"decision": "maybe"})).status_code == 422

    done = await client.patch(url(world, f"prefill/{first}"), json={"decision": "accepted"})
    assert done.status_code == 200, done.status_code
    assert (done.json()["status"], done.json()["value"]) == ("accepted", SUGGESTED[0][1])
    assert (await client.patch(url(world, f"prefill/{first}"), json={"decision": "dismissed"})).status_code == 409
    dismissed = await client.patch(url(world, f"prefill/{second}"), json={"decision": "dismissed"})
    assert dismissed.status_code == 200 and dismissed.json()["status"] == "dismissed"

    # accepting records the choice; the case changes only when the expert saves the form
    assert (await client.get(url(world, "instructing-parties"))).json()["current"] == []


async def test_without_a_model_the_prefill_job_pauses(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, storage: CaseFileStorage
) -> None:
    configure(SetupServices(kit=None, ocr=None, storage=storage, crypto=None, packs=default_library()))
    try:
        client = await client_for(world["expert"])
        await add_letter(client, world)
        with pytest.raises(ProcessingPaused):
            await run(worker_db, world, await start(client, world))
    finally:
        configure(None)
