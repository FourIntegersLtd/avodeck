"""Completing an upload publishes `DocumentUploaded` inside the request's transaction.

Slice A subscribes to queue the virus scan and processing; the subscriber gets the same connection,
so its jobs commit with the document or not at all. A failing subscriber fails the request: no
document is registered and the upload can be completed again. Uploads of later records say so
(`append`), and the event carries it.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator

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

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: SupabaseStorage) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=CaseCrypto(LocalKeyProvider.generate()))


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    return {"expert": expert, "case": seed.case(org, expert)}


@pytest.fixture
def seen() -> Iterator[list[tuple[DocumentUploaded, uuid.UUID | None]]]:
    got: list[tuple[DocumentUploaded, uuid.UUID | None]] = []

    async def listener(conn: AsyncConnection, event: DocumentUploaded) -> None:
        cur = await conn.execute("select app.current_person_id()")
        row = await cur.fetchone()
        got.append((event, row[0] if row else None))

    events.subscribe_in_transaction(DocumentUploaded)(listener)
    try:
        yield got
    finally:
        events.unsubscribe(DocumentUploaded, listener)


async def upload(client_for: ClientFor, team: dict[str, uuid.UUID], **extra: object) -> tuple[object, str]:
    file = SyntheticFile(1 * MB, seed=23)
    client = await client_for(team["expert"])
    body = {"kind": "records", "filename": "bundle.pdf", "size": file.size, "sha256": file.sha256(), **extra}
    started = await client.post(f"/v1/cases/{team['case']}/uploads", json=body)
    assert started.status_code == 201, started.text
    upload_id = started.json()["upload_id"]
    sent = await client.put(
        f"/v1/uploads/{upload_id}/chunks/1", content=file.chunk(1), headers={"content-type": "application/octet-stream"}
    )
    assert sent.status_code == 200
    return client, upload_id


async def test_completing_publishes_the_event_once_on_the_request_connection(
    client_for: ClientFor, team: dict[str, uuid.UUID], seen: list[tuple[DocumentUploaded, uuid.UUID | None]]
) -> None:
    client, upload_id = await upload(client_for, team)
    done = await client.post(f"/v1/uploads/{upload_id}/complete")  # type: ignore[attr-defined]
    assert done.status_code == 201, done.text
    again = await client.post(f"/v1/uploads/{upload_id}/complete")  # type: ignore[attr-defined]
    assert again.status_code == 200
    document = uuid.UUID(done.json()["document_id"])
    assert seen == [(DocumentUploaded(case_id=team["case"], document_id=document, appended=False), team["expert"])]


async def test_an_upload_of_later_records_says_so_in_the_event(
    client_for: ClientFor, team: dict[str, uuid.UUID], seen: list[tuple[DocumentUploaded, uuid.UUID | None]]
) -> None:
    client, upload_id = await upload(client_for, team, append=True)
    done = await client.post(f"/v1/uploads/{upload_id}/complete")  # type: ignore[attr-defined]
    assert done.status_code == 201, done.text
    [(event, _)] = seen
    assert event.appended is True
    assert event.document_id == uuid.UUID(done.json()["document_id"])


async def test_a_failing_subscriber_fails_the_request_and_nothing_is_registered(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    async def broken(conn: AsyncConnection, event: DocumentUploaded) -> None:
        raise ApiError("unavailable", "Processing could not be queued.", 503)

    client, upload_id = await upload(client_for, team)
    events.subscribe_in_transaction(DocumentUploaded)(broken)
    try:
        failed = await client.post(f"/v1/uploads/{upload_id}/complete")  # type: ignore[attr-defined]
    finally:
        events.unsubscribe(DocumentUploaded, broken)
    assert failed.status_code == 503
    assert admin.execute("select count(*) from app.documents where case_id = %s", (team["case"],)).fetchone() == (0,)
    assert admin.execute("select status from app.uploads where id = %s", (upload_id,)).fetchone() == ("open",)
    retried = await client.post(f"/v1/uploads/{upload_id}/complete")  # type: ignore[attr-defined]
    assert retried.status_code == 201, "once the subscriber works, completing again succeeds"
