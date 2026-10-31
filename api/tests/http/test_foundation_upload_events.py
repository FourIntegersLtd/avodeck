"""Registering a document publishes `DocumentUploaded` inside the transaction that makes it.

Completing an upload only claims it (202) and queues `finalize_upload`; the job verifies the stored
parts and registers the document, so the event is published on the job's connection, in the same
transaction as the document row. Slice A subscribes there to queue the virus scan and processing, so
its jobs commit with the document or not at all. A failing subscriber fails the job: no document is
registered, the upload stays `finalizing`, and the next attempt succeeds once the subscriber works.
Uploads of later records say so (`append`), and the event carries it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from psycopg import AsyncConnection

from app.core.contracts.events import DocumentUploaded, events
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.errors import ApiError
from app.core.storage import SupabaseStorage
from tests.support.db import ClientFor
from tests.support.files import MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import complete, put_all, row, run_finalize

pytestmark = pytest.mark.anyio
FILE_SHA = SyntheticFile(1 * MB, seed=23).sha256()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: SupabaseStorage, crypto: CaseCrypto) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=crypto)


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    return {"expert": expert, "case": seed.case(org, expert)}


@pytest.fixture
def seen() -> Iterator[list[tuple[DocumentUploaded, int | None]]]:
    """Each event, with the number of document rows its own connection can see for that id.

    One means the subscriber is inside the transaction that inserted the document: no other
    connection can see that row until it commits.
    """
    got: list[tuple[DocumentUploaded, int | None]] = []

    async def listener(conn: AsyncConnection, event: DocumentUploaded) -> None:
        cur = await conn.execute("select count(*) from app.documents where id = %s", (event.document_id,))
        counted = await cur.fetchone()
        got.append((event, counted[0] if counted else None))

    events.subscribe_in_transaction(DocumentUploaded)(listener)
    try:
        yield got
    finally:
        events.unsubscribe(DocumentUploaded, listener)


async def upload(
    client_for: ClientFor, team: dict[str, uuid.UUID], **extra: object
) -> tuple[httpx.AsyncClient, str]:
    """Start an upload, send its one part, and claim it: the job is queued and the upload is finalizing."""
    file = SyntheticFile(1 * MB, seed=23)
    client = await client_for(team["expert"])
    body = {"kind": "records", "filename": "bundle.pdf", "size": file.size, "sha256": file.sha256(), **extra}
    started = await client.post(f"/v1/cases/{team['case']}/uploads", json=body)
    assert started.status_code == 201, started.text
    upload_id = str(started.json()["upload_id"])
    await put_all(client, upload_id, file)
    claimed = await complete(client, upload_id, FILE_SHA)
    assert claimed.status_code == 202, claimed.text
    return client, upload_id


async def test_registering_publishes_the_event_once_on_the_jobs_connection(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    seen: list[tuple[DocumentUploaded, int | None]],
) -> None:
    client, upload_id = await upload(client_for, team)
    await run_finalize(worker_dsn, admin, upload_id, storage, crypto)
    document = row(admin, "select document_id from app.uploads where id = %s", upload_id)
    assert document is not None and document[0] is not None
    expected = DocumentUploaded(case_id=team["case"], document_id=uuid.UUID(str(document[0])), appended=False)
    assert seen == [(expected, 1)]
    again = await complete(client, upload_id, FILE_SHA)
    assert again.status_code == 200, "a repeat call gives the same document, with nothing re-read"
    assert seen == [(expected, 1)], "and publishes nothing a second time"


async def test_an_upload_of_later_records_says_so_in_the_event(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    seen: list[tuple[DocumentUploaded, int | None]],
) -> None:
    _, upload_id = await upload(client_for, team, append=True)
    await run_finalize(worker_dsn, admin, upload_id, storage, crypto)
    [(event, _)] = seen
    assert event.appended is True
    document = row(admin, "select document_id from app.uploads where id = %s", upload_id)
    assert document is not None
    assert event.document_id == uuid.UUID(str(document[0]))


async def test_a_failing_subscriber_fails_the_job_and_nothing_is_registered(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    async def broken(conn: AsyncConnection, event: DocumentUploaded) -> None:
        raise ApiError("unavailable", "Processing could not be queued.", 503)

    _, upload_id = await upload(client_for, team)
    events.subscribe_in_transaction(DocumentUploaded)(broken)
    try:
        with pytest.raises(ApiError):  # raised out of the job, so the queue retries it
            await run_finalize(worker_dsn, admin, upload_id, storage, crypto)
    finally:
        events.unsubscribe(DocumentUploaded, broken)
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (0,)
    assert row(admin, "select status from app.uploads where id = %s", upload_id) == ("finalizing",)
    await run_finalize(worker_dsn, admin, upload_id, storage, crypto, attempt=2)
    document = row(admin, "select status, document_id from app.uploads where id = %s", upload_id)
    assert document is not None and document[0] == "complete"
    assert document[1] is not None, "once the subscriber works, the next attempt registers the document"
