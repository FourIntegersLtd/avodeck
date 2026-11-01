"""Chunked uploads to private Storage (architecture 5.3, screen 04; foundation brief).

A file goes up in 8 MB chunks through FastAPI only (rule 4: no storage links reach the browser).
Each chunk is stored encrypted under the case's key (ADR 0010). A dropped chunk is simply sent
again; completing checks the whole file's SHA-256 and registers the `documents` row. The file
comes back the same way, streamed through FastAPI with `Cache-Control: private, no-store`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.storage import CaseFileStorage
from tests.support.db import ClientFor
from tests.support.files import CHUNK, MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import run_finalize

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
    p = {n: seed.person() for n in ("expert", "viewer", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "owner", p["viewer"]: "viewer"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["viewer"]: "viewer"})
    return p


async def start(client: httpx.AsyncClient, case: uuid.UUID, file: SyntheticFile, **overrides: object) -> httpx.Response:
    body = {"kind": "records", "filename": "bundle.pdf", "size": file.size, "sha256": file.sha256(), **overrides}
    return await client.post(f"/v1/cases/{case}/uploads", json=body)


async def put_chunk(client: httpx.AsyncClient, upload: str, n: int, data: bytes, **headers: str) -> httpx.Response:
    return await client.put(
        f"/v1/uploads/{upload}/chunks/{n}",
        content=data,
        headers={
            "content-type": "application/octet-stream",
            "x-chunk-sha256": hashlib.sha256(data).hexdigest(),
            **headers,
        },
    )


async def finish(client: httpx.AsyncClient, upload: str, file: SyntheticFile) -> httpx.Response:
    return await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})


def no_storage_link(resp: httpx.Response, auth_env: dict[str, str]) -> None:
    text = resp.text.lower()
    assert "storage/v1" not in text
    assert auth_env["SUPABASE_URL"].lower() not in text
    assert "supabase" not in text


async def test_a_300_mb_pdf_uploads_in_8_mb_chunks_resumes_after_a_dropped_chunk_and_matches_its_sha256(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    admin: psycopg.Connection,
    auth_env: dict[str, str],
) -> None:
    file = SyntheticFile(300 * MB)
    client = await client_for(team["expert"])

    resp = await start(client, team["case"], file)
    assert resp.status_code == 201, resp.text
    no_storage_link(resp, auth_env)
    upload = resp.json()
    assert upload["chunk_size"] == CHUNK == 8 * MB
    assert upload["chunk_count"] == file.count == 38
    assert upload["received"] == []
    upload_id = upload["upload_id"]

    for n, data in file.chunks():
        if n == 20:  # the connection drops part-way through chunk 20
            resp = await put_chunk(client, upload_id, n, data[: len(data) // 2])
            assert resp.status_code == 422, resp.text
            assert resp.json()["error"]["code"] == "chunk_size_wrong"
            continue
        resp = await put_chunk(client, upload_id, n, data)
        assert resp.status_code == 200, (n, resp.text)
        no_storage_link(resp, auth_env)

    resp = await client.get(f"/v1/uploads/{upload_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["missing"] == [20]
    assert len(resp.json()["received"]) == 37

    resp = await finish(client, upload_id, file)
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "chunks_missing"
    assert resp.json()["error"]["missing"] == [20]

    resp = await put_chunk(client, upload_id, 20, file.chunk(20))
    assert resp.status_code == 200, resp.text

    resp = await finish(client, upload_id, file)
    assert resp.status_code == 202, resp.text
    assert resp.json() == {"status": "finalizing"}
    assert admin.execute("select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", (team["case"],)).fetchone() == (0,)
    await run_finalize(worker_dsn, admin, upload_id, storage, crypto)

    resp = await finish(client, upload_id, file)
    assert resp.status_code == 200, resp.text
    no_storage_link(resp, auth_env)
    done = resp.json()
    assert done["sha256"] == file.sha256()
    assert done["size"] == file.size
    document = uuid.UUID(done["document_id"])

    row = admin.execute(
        "select c.id, d.kind, d.filename, d.sha256, d.storage_path, d.uploaded_by"
        " from app.documents d join app.cases c on c.bundle_id = d.bundle_id where d.id = %s",
        (document,),
    ).fetchone()
    assert row is not None
    assert row[:4] == (team["case"], "records", "bundle.pdf", file.sha256())
    assert row[4], "the document records where its file is stored"
    assert row[5] == team["expert"]

    # Stored encrypted: what sits in Storage is not the chunk that was sent.
    paths = await storage.list(row[4])
    assert len(paths) == file.count
    stored = await storage.get(sorted(paths)[0])
    assert stored != file.chunk(1)
    assert file.chunk(1)[:64] not in stored

    # And it comes back, streamed through FastAPI, byte for byte.
    digest, size = hashlib.sha256(), 0
    async with client.stream("GET", f"/v1/cases/{team['case']}/documents/{document}/file") as resp:
        assert resp.status_code == 200
        assert resp.headers["cache-control"] == "private, no-store"
        async for part in resp.aiter_bytes():
            digest.update(part)
            size += len(part)
    assert (digest.hexdigest(), size) == (file.sha256(), file.size)


async def test_completing_records_an_audit_row_and_a_second_complete_returns_the_same_document(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(3 * MB, seed=11)
    client = await client_for(team["expert"])
    upload_id = (await start(client, team["case"], file)).json()["upload_id"]
    assert (await put_chunk(client, upload_id, 1, file.chunk(1))).status_code == 200
    claimed = await finish(client, upload_id, file)
    assert claimed.status_code == 202, claimed.text
    again_early = await finish(client, upload_id, file)  # a repeat while the job is queued: not a new claim
    assert again_early.status_code == 202, again_early.text
    claims = admin.execute(
        "select person_id from app.audit_log where action = 'upload.completed' and target = %s",
        (f"upload:{upload_id}",),
    ).fetchall()
    assert claims == [(team["expert"],)]
    await run_finalize(worker_dsn, admin, upload_id, storage, crypto)
    first = await finish(client, upload_id, file)
    assert first.status_code == 200, first.text
    again = await finish(client, upload_id, file)
    assert again.status_code == 200, again.text
    assert again.json()["document_id"] == first.json()["document_id"]
    count = admin.execute("select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", (team["case"],)).fetchone()
    assert count == (1,)
    audit = admin.execute(
        "select action, target, person_id from app.audit_log where case_id = %s order by seq desc limit 1",
        (team["case"],),
    ).fetchone()
    # Written by the job, which has no signed-in person (the audit policy for the worker requires none);
    # who sent the file is kept on the document.
    assert audit == ("document.uploaded", f"document:{first.json()['document_id']}", None)
    sender = admin.execute("select uploaded_by from app.documents where id = %s", (first.json()["document_id"],))
    assert sender.fetchone() == (team["expert"],)


async def test_a_file_whose_sha256_does_not_match_is_refused_and_not_registered(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(2 * MB, seed=12)
    client = await client_for(team["expert"])
    upload_id = (await start(client, team["case"], file, sha256="a" * 64)).json()["upload_id"]
    await put_chunk(client, upload_id, 1, file.chunk(1))
    resp = await finish(client, upload_id, file)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "file_hash_mismatch"
    # Refused in the request: no job was queued, the upload is still open and no document exists.
    state = admin.execute("select status, finalize_job_id from app.uploads where id = %s", (upload_id,)).fetchone()
    assert state == ("open", None)
    assert admin.execute("select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", (team["case"],)).fetchone() == (0,)


async def test_a_chunk_that_does_not_match_its_own_checksum_is_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    file = SyntheticFile(2 * MB, seed=13)
    client = await client_for(team["expert"])
    upload_id = (await start(client, team["case"], file)).json()["upload_id"]
    resp = await put_chunk(client, upload_id, 1, file.chunk(1), **{"x-chunk-sha256": "b" * 64})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "chunk_corrupted"
    ok = await put_chunk(
        client, upload_id, 1, file.chunk(1), **{"x-chunk-sha256": hashlib.sha256(file.chunk(1)).hexdigest()}
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["received"] == [1]


@pytest.mark.parametrize("n", [0, 2])
async def test_a_chunk_number_outside_the_file_is_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID], n: int
) -> None:
    file = SyntheticFile(MB, seed=14)
    client = await client_for(team["expert"])
    upload_id = (await start(client, team["case"], file)).json()["upload_id"]
    resp = await put_chunk(client, upload_id, n, file.chunk(1))
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "chunk_out_of_range"


async def test_files_over_the_size_limit_or_with_a_bad_checksum_cannot_start(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    client = await client_for(team["expert"])
    too_big = await start(client, team["case"], SyntheticFile(MB), size=500 * MB + 1)
    assert too_big.status_code == 422, too_big.text
    assert too_big.json()["error"]["code"] == "file_too_large"
    bad = await start(client, team["case"], SyntheticFile(MB), sha256="not-a-checksum")
    assert bad.status_code == 422, bad.text


async def test_a_viewer_cannot_upload(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    resp = await start(await client_for(team["viewer"]), team["case"], SyntheticFile(MB))
    assert resp.status_code == 403, resp.text
    assert resp.json()["error"]["code"] == "forbidden"


async def test_someone_without_access_to_the_case_cannot_see_send_to_or_complete_its_upload(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(MB, seed=15)
    upload_id = (await start(await client_for(team["expert"]), team["case"], file)).json()["upload_id"]
    outsider = await client_for(team["outsider"])
    for resp in (
        await outsider.get(f"/v1/uploads/{upload_id}"),
        await put_chunk(outsider, upload_id, 1, file.chunk(1)),
        await outsider.post(f"/v1/uploads/{upload_id}/complete"),
        await outsider.get(f"/v1/uploads/{uuid.uuid4()}"),
    ):
        assert resp.status_code == 404, resp.text
        assert resp.json()["error"]["code"] == "not_found"
    received = admin.execute("select received from app.uploads where id = %s", (upload_id,)).fetchone()
    assert received == ([],)


async def test_a_document_without_a_stored_file_is_not_found(
    client_for: ClientFor, team: dict[str, uuid.UUID], seed: Seed
) -> None:
    document = seed.document(team["case"])
    resp = await (await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/documents/{document}/file")
    assert resp.status_code == 404, resp.text
