"""The `finalize_upload` job and the upload routes around it (fix/upload-hardening stream A; F43).

Complete claims the upload and returns 202; the job re-reads the stored parts one at a time, checking each
part's SHA-256 and the whole file's, then makes the document, audits it and publishes `DocumentUploaded`,
all in one transaction. A part that is gone sends the upload back to `open` for the browser to resend; a storage
or network error is retried with nothing changed; an unreadable part or a hash or size mismatch fails the
upload for good. No log line or audit row carries any file content.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.contracts.events import DocumentUploaded, events
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.storage import CaseFileStorage, StorageError, upload_prefix
from tests.support.db import ClientFor
from tests.support.files import MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import complete, put, put_all, row, run_finalize, start

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage, crypto: CaseCrypto) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=crypto)


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    return {"expert": expert, "case": seed.case(org, expert)}


def path_of(team: dict[str, uuid.UUID], upload: str, n: int) -> str:
    return f"{upload_prefix(team['case'], uuid.UUID(upload))}/{n:05d}"


async def claimed(client: httpx.AsyncClient, team: dict[str, uuid.UUID], file: SyntheticFile) -> str:
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    assert (await complete(client, upload, file.sha256())).status_code == 202
    return upload


async def test_a_missing_object_sends_the_upload_back_to_open_and_a_resend_completes_it(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=61)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    await storage.delete([path_of(team, upload, 2)])
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(admin, "select status, received, chunk_sha256 ? '2' from app.uploads where id = %s", upload) == (
        "open",
        [1, 3],
        False,
    )
    seen = (await client.get(f"/v1/uploads/{upload}")).json()
    assert (seen["status"], seen["missing"]) == ("open", [2])
    assert (await put(client, upload, 2, file.chunk(2))).status_code == 200
    assert (await complete(client, upload, file.sha256())).status_code == 202
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)
    assert row(admin, "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", team["case"]) == (1,)


async def test_a_storage_error_is_raised_for_a_retry_and_changes_nothing(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = SyntheticFile(17 * MB, seed=62)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    before = row(admin, "select status, received, chunk_sha256, finalize_job_id from app.uploads where id = %s", upload)
    real_get = storage.get

    async def broken(path: str) -> bytes:
        raise StorageError("storage answered 503")

    monkeypatch.setattr(storage, "get", broken)
    with pytest.raises(StorageError):
        await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert (
        row(admin, "select status, received, chunk_sha256, finalize_job_id from app.uploads where id = %s", upload)
        == before
    )
    assert row(admin, "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", team["case"]) == (0,)
    monkeypatch.setattr(storage, "get", real_get)
    await run_finalize(worker_dsn, admin, upload, storage, crypto, attempt=2)
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)


async def test_a_crash_after_the_first_chunk_then_a_retry_makes_one_document(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = SyntheticFile(24 * MB, seed=63)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    real_get = storage.get
    calls = 0

    async def crashing(path: str) -> bytes:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("the worker went away")
        return await real_get(path)

    monkeypatch.setattr(storage, "get", crashing)
    with pytest.raises(RuntimeError):
        await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("finalizing",)
    await run_finalize(worker_dsn, admin, upload, storage, crypto, attempt=2)
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)
    assert row(admin, "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", team["case"]) == (1,)


async def test_an_unreadable_object_fails_the_upload_audits_it_and_leaves_the_parts(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    file = SyntheticFile(17 * MB, seed=64)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    await storage.put(path_of(team, upload, 2), b"not what was stored" * 10)  # no longer opens under the case's key
    with caplog.at_level(logging.DEBUG):
        await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(admin, "select status, failure from app.uploads where id = %s", upload) == ("failed", "chunk_unreadable")
    assert row(
        admin,
        "select count(*) from app.audit_log where target = %s and action = 'upload.chunk_unreadable'",
        f"upload:{upload}",
    ) == (1,)
    assert row(admin, "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", team["case"]) == (0,)
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count
    assert "not what was stored" not in caplog.text


async def test_a_byte_count_that_differs_from_the_declared_size_fails_the_upload(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=65)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    admin.execute("update app.uploads set size = size + 1 where id = %s", (upload,))  # still three parts
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(admin, "select status, failure from app.uploads where id = %s", upload) == ("failed", "size_mismatch")
    assert row(admin, "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", team["case"]) == (0,)


async def test_the_uploaded_event_is_published_exactly_once(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=66)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    seen: list[DocumentUploaded] = []

    async def count(conn: object, event: DocumentUploaded) -> None:
        seen.append(event)

    events.subscribe_in_transaction(DocumentUploaded)(count)
    try:
        await run_finalize(worker_dsn, admin, upload, storage, crypto)
        await run_finalize(worker_dsn, admin, upload, storage, crypto, attempt=2)  # already moved on: nothing to do
        assert (await complete(client, upload, file.sha256())).status_code == 200
    finally:
        events.unsubscribe(DocumentUploaded, count)
    assert [(e.case_id, e.appended) for e in seen] == [(team["case"], False)]
    document = row(admin, "select document_id from app.uploads where id = %s", upload)
    assert document is not None and seen[0].document_id == document[0]
    assert row(
        admin, "select count(*) from app.audit_log where case_id = %s and action = 'document.uploaded'", team["case"]
    ) == (1,)


async def test_a_failed_job_lets_the_next_complete_claim_the_upload_again(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=67)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    old = row(admin, "select finalize_job_id from app.uploads where id = %s", upload)
    assert old is not None
    admin.execute("update app.jobs set status = 'running' where id = %s", (old[0],))
    admin.execute("update app.jobs set status = 'failed', error_code = 'job_error' where id = %s", (old[0],))
    again = await complete(client, upload, file.sha256())
    assert (again.status_code, again.json()) == (202, {"status": "finalizing"})
    new = row(admin, "select finalize_job_id from app.uploads where id = %s", upload)
    assert new is not None and new[0] != old[0]
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)


# --- start and part validation ---


async def test_an_empty_file_is_refused_with_its_own_code(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    client = await client_for(team["expert"])
    resp = await client.post(
        f"/v1/cases/{team['case']}/uploads",
        json={"kind": "records", "filename": "a.pdf", "size": 0, "sha256": "a" * 64},
    )
    assert (resp.status_code, resp.json()["error"]["code"]) == (422, "file_empty"), resp.text


@pytest.mark.parametrize("name", ["a\x00b.pdf", "a\nb.pdf", "tab\t.pdf", "del\x7f.pdf"])
async def test_a_filename_with_control_characters_is_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID], name: str
) -> None:
    client = await client_for(team["expert"])
    resp = await client.post(
        f"/v1/cases/{team['case']}/uploads", json={"kind": "records", "filename": name, "size": 10, "sha256": "a" * 64}
    )
    assert resp.status_code == 422, resp.text


async def test_a_person_may_have_twenty_uploads_in_progress_and_no_more(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    client = await client_for(team["expert"])
    body = {"kind": "records", "filename": "a.pdf", "size": 10, "sha256": "a" * 64}
    for _ in range(20):
        assert (await client.post(f"/v1/cases/{team['case']}/uploads", json=body)).status_code == 201
    resp = await client.post(f"/v1/cases/{team['case']}/uploads", json=body)
    assert (resp.status_code, resp.json()["error"]["code"]) == (429, "too_many_uploads"), resp.text


async def test_a_part_larger_than_expected_is_refused_before_it_is_read(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=68)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    big = b"x" * (8 * MB + 1)
    resp = await put(client, upload, 1, big)
    assert (resp.status_code, resp.json()["error"]["code"]) == (413, "chunk_too_large"), resp.text
    assert row(admin, "select received from app.uploads where id = %s", upload) == ([],)


async def test_a_part_streamed_without_a_length_is_cut_off_at_the_expected_size(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=69)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)

    async def body() -> AsyncIterator[bytes]:
        for _ in range(9):  # nine megabytes, sent chunked with no Content-Length
            yield b"x" * MB

    resp = await client.put(f"/v1/uploads/{upload}/chunks/1", content=body(), headers={"x-chunk-sha256": "a" * 64})
    assert (resp.status_code, resp.json()["error"]["code"]) == (413, "chunk_too_large"), resp.text
    assert row(admin, "select received from app.uploads where id = %s", upload) == ([],)


async def test_a_download_whose_object_is_gone_is_a_404_before_any_bytes_stream(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=70)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    document = (await complete(client, upload, file.sha256())).json()["document_id"]
    await storage.delete([path_of(team, upload, 1)])
    resp = await client.get(f"/v1/cases/{team['case']}/documents/{document}/file")
    assert resp.status_code == 404, resp.text


async def test_a_reopen_is_audited_without_a_person_and_names_only_the_upload(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=71)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    await storage.delete([path_of(team, upload, 2)])
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    found = admin.execute(
        "select person_id, case_id, action, target from app.audit_log where action = 'upload.chunk_missing'"
        " and target = %s",
        (f"upload:{upload}",),
    ).fetchall()
    assert found == [(None, team["case"], "upload.chunk_missing", f"upload:{upload}")]


async def test_a_dead_job_does_not_revive_an_upload_past_its_expiry(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(3 * MB, seed=72)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    before = row(admin, "select finalize_job_id, finalizing_at from app.uploads where id = %s", upload)
    admin.execute("set session_replication_role = replica")  # the queue's guard only allows the real moves
    admin.execute("update app.jobs set status = 'failed' where id = %s", (before[0],))  # type: ignore[index]
    admin.execute("set session_replication_role = default")
    admin.execute("update app.uploads set expires_at = now() - interval '1 hour' where id = %s", (upload,))
    resp = await complete(client, upload, file.sha256())
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "upload_closed"
    assert row(admin, "select finalize_job_id, finalizing_at from app.uploads where id = %s", upload) == before


async def test_streaming_holds_no_connection_so_a_one_connection_pool_still_finishes_the_job(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    """The key is reloaded in its own short transaction (here before every part, as its cache never holds)."""
    from datetime import timedelta

    from tests.support.uploads import worker_database

    file = SyntheticFile(17 * MB, seed=73)
    client = await client_for(team["expert"])
    upload = await claimed(client, team, file)
    forgetful = CaseCrypto(crypto.provider, ttl=timedelta(0))
    found = row(admin, "select finalize_job_id, case_id from app.uploads where id = %s", upload)
    assert found is not None
    from app.core.jobs import JobContext
    from app.core.uploads import finalize

    async with worker_database(worker_dsn, max_size=1) as database:
        ctx = JobContext(database, found[0], found[1], "finalize_upload", 1)  # type: ignore[arg-type]
        await asyncio.wait_for(finalize(ctx, storage, forgetful), timeout=30)
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)
