"""A letter is read (OCR, then T-06) only once its virus scan is recorded clean (audit 12, V2e).

A letter with no verdict yet waits and is read once the scan has answered; a flagged one is never read and the
expert is told it was blocked.
"""

# ruff: noqa: F811
from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from app.core.ai.bedrock import RecordedBedrock
from app.core.db import Database
from app.core.errors import ProcessingPaused
from tests.http.test_setup_questions_routes import (  # noqa: F401  (fixtures and helpers)
    LETTER,
    PARTY,
    PARTY_ROLE,
    app,
    crypto,
    model,
    run_job,
    storage,
    upload,
    url,
    worker_db,
    world,
)
from tests.support.db import ClientFor
from tests.support.uploads import record_clean_scan

pytestmark = pytest.mark.anyio


async def _letter(client: Any, world: dict[str, uuid.UUID]) -> dict[str, Any]:
    """A letter whose file has no scan verdict yet."""
    document = await upload(client, world["case"], LETTER, "letter.pdf", scanned=False)
    added = await client.post(url(world, "instructions"), json={"kind": "letter", "document_id": document})
    assert added.status_code == 201, added.status_code
    return dict(added.json())


def _flag(admin: psycopg.Connection, document: str) -> None:
    admin.execute(
        "insert into app.document_scans (document_id, bundle_id, clean, signature)"
        " select id, bundle_id, false, 'Test-Signature' from app.documents where id = %s",
        (document,),
    )


async def test_a_letter_not_scanned_yet_waits_and_is_read_once_the_scan_is_clean(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    party = await client.post(url(world, "parties"), json={"role_key": PARTY_ROLE, "name": PARTY})
    assert party.status_code == 201
    added = await _letter(client, world)

    with pytest.raises(ProcessingPaused) as paused:
        await run_job(worker_db, world, added["job_id"])
    assert paused.value.reason == "service_busy"  # the queue puts the job back after a back-off
    assert not model.requests, "nothing was sent to the AI before the scan answered"
    assert (await client.get(url(world, "questions"))).json()["reading"] is True

    record_clean_scan(added["document_id"])
    await run_job(worker_db, world, added["job_id"])
    assert len(model.requests) == 1
    got = (await client.get(url(world, "questions"))).json()
    assert got["reading"] is False and got["questions"]


async def test_a_flagged_letter_is_never_read_and_the_expert_is_told(
    client_for: ClientFor,
    world: dict[str, uuid.UUID],
    worker_db: Database,
    model: RecordedBedrock,
    admin: psycopg.Connection,
) -> None:
    client = await client_for(world["expert"])
    added = await _letter(client, world)
    _flag(admin, added["document_id"])

    await run_job(worker_db, world, added["job_id"])  # nothing to wait for: it ends without reading
    assert not model.requests
    got = (await client.get(url(world, "questions"))).json()
    assert got["reading"] is False and got["read_failed"] is False and got["questions"] == []
    assert "setup.questions.warning.letter_blocked" in got["warnings"]
    blocked = await client.post(url(world, "questions/confirm-all"))
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "letter_blocked"


async def test_the_letter_pictures_follow_the_scan_verdict(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["expert"])
    added = await _letter(client, world)
    pages = url(world, f"instructions/{added['id']}/pages")

    assert (await client.get(pages)).status_code == 423
    _flag(admin, added["document_id"])
    assert (await client.get(pages)).status_code == 409
