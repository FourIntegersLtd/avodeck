"""A stored upload is provably the file the person sent (architecture 5.3; failure modes F30, F31).

Every part carries its own SHA-256, checked on arrival against the bytes received. Completing carries
the whole file's SHA-256, checked against a streamed re-read of the stored parts. A part sent under the
wrong number passes the per-part check (the sender hashed the same wrong bytes) but not the whole-file
check, so the upload is closed as failed and no document is made.
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
from app.core.storage import SupabaseStorage, upload_prefix
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


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def start(client: httpx.AsyncClient, case: uuid.UUID, file: SyntheticFile) -> str:
    body = {"kind": "records", "filename": "bundle.pdf", "size": file.size, "sha256": file.sha256()}
    resp = await client.post(f"/v1/cases/{case}/uploads", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["upload_id"])


async def put(client: httpx.AsyncClient, upload: str, n: int, data: bytes, digest: str | None = None) -> httpx.Response:
    headers = {"content-type": "application/octet-stream"}
    if digest is not None:
        headers["x-chunk-sha256"] = digest
    return await client.put(f"/v1/uploads/{upload}/chunks/{n}", content=data, headers=headers)


async def put_all(client: httpx.AsyncClient, upload: str, file: SyntheticFile) -> None:
    for n, data in file.chunks():
        assert (await put(client, upload, n, data, sha(data))).status_code == 200


def row(admin: psycopg.Connection, sql: str, *args: object) -> tuple[object, ...] | None:
    return admin.execute(sql, args).fetchone()  # type: ignore[arg-type]


async def test_a_chunk_with_the_right_hash_is_accepted_and_its_hash_is_kept(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=31)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    resp = await put(client, upload, 1, file.chunk(1), sha(file.chunk(1)).upper())  # compared without regard to case
    assert resp.status_code == 200, resp.text
    assert resp.json()["received"] == [1]
    assert row(admin, "select chunk_sha256 from app.uploads where id = %s", upload) == ({"1": sha(file.chunk(1))},)


async def test_a_chunk_with_the_wrong_hash_is_refused_and_nothing_is_stored(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    file = SyntheticFile(17 * MB, seed=32)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    resp = await put(client, upload, 1, file.chunk(1), "b" * 64)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "chunk_corrupted"
    assert await storage.list(upload_prefix(team["case"], uuid.UUID(upload))) == []
    assert row(admin, "select received, chunk_sha256 from app.uploads where id = %s", upload) == ([], {})


@pytest.mark.parametrize("digest", [None, "", "abc", "g" * 64, "a" * 63, "a" * 65])
async def test_a_chunk_with_a_missing_or_malformed_hash_is_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, digest: str | None
) -> None:
    file = SyntheticFile(17 * MB, seed=33)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    resp = await put(client, upload, 1, file.chunk(1), digest)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "chunk_sha_missing"
    assert row(admin, "select received from app.uploads where id = %s", upload) == ([],)


async def test_the_short_last_chunk_is_accepted_with_its_hash(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    file = SyntheticFile(17 * MB, seed=34)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    last = file.chunk(3)
    assert len(last) == 1 * MB
    resp = await put(client, upload, 3, last, sha(last))
    assert resp.status_code == 200, resp.text
    assert resp.json()["received"] == [3]


async def test_sending_a_chunk_again_keeps_one_entry_and_replaces_its_hash(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=35)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    for _ in range(2):
        resp = await put(client, upload, 2, file.chunk(2), sha(file.chunk(2)))
        assert resp.json()["received"] == [2]
    other = b"x" * len(file.chunk(2))  # a different part sent under the same number: its hash replaces the old one
    assert (await put(client, upload, 2, other, sha(other))).status_code == 200
    assert row(admin, "select received, chunk_sha256 from app.uploads where id = %s", upload) == ([2], {"2": sha(other)})


async def test_receiving_a_chunk_touches_only_its_own_upload(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=36)
    client = await client_for(team["expert"])
    first, second = await start(client, team["case"], file), await start(client, team["case"], file)
    assert (await put(client, first, 1, file.chunk(1), sha(file.chunk(1)))).status_code == 200
    assert row(admin, "select received, chunk_sha256 from app.uploads where id = %s", second) == ([], {})
    assert row(admin, "select received from app.uploads where id = %s", first) == ([1],)


async def test_completing_with_the_right_file_hash_makes_a_document_that_keeps_its_fingerprint(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=37)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    resp = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})
    assert resp.status_code == 201, resp.text
    doc = resp.json()["document_id"]
    assert row(admin, "select sha256, sha256_verified_at is not null from app.documents where id = %s", doc) == (
        file.sha256(),
        True,
    )


async def test_completing_with_a_wrong_file_hash_is_refused_and_the_upload_is_closed(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=38)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    resp = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": "c" * 64})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "file_hash_mismatch"
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("failed", None)
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (0,)
    again = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "upload_closed"
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (0,)


async def test_the_right_size_but_wrong_bytes_are_caught_by_the_whole_file_hash(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(24 * MB, seed=39)  # three whole chunks
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    for n in (1, 2):
        assert (await put(client, upload, n, file.chunk(n), sha(file.chunk(n)))).status_code == 200
    # Chunk 2's bytes under chunk 3's number: right size, and the hash matches the bytes sent.
    assert (await put(client, upload, 3, file.chunk(2), sha(file.chunk(2)))).status_code == 200
    resp = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "file_hash_mismatch"
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (0,)
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("failed", None)


@pytest.mark.parametrize("body", [None, {}, {"sha256": "nope"}, {"sha256": "A" * 63}])
async def test_completing_without_a_well_formed_file_hash_is_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, body: dict[str, str] | None
) -> None:
    file = SyntheticFile(17 * MB, seed=40)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    resp = await client.post(f"/v1/uploads/{upload}/complete", **({"json": body} if body is not None else {}))
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "file_sha_missing"
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("open",)


async def test_completing_with_chunks_missing_is_refused_and_leaves_the_upload_open(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=41)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    for n in (1, 3):
        assert (await put(client, upload, n, file.chunk(n), sha(file.chunk(n)))).status_code == 200
    resp = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "chunks_missing"
    assert resp.json()["error"]["missing"] == [2]
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("open",)


async def test_completing_twice_returns_the_same_document_without_a_second_one(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=42)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    first = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})
    again = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})
    assert (first.status_code, again.status_code) == (201, 200)
    assert again.json()["document_id"] == first.json()["document_id"]
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (1,)
