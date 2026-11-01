"""A stored upload is provably the file the person sent (architecture 5.3; failure modes F30, F31).

Every part carries its own SHA-256, checked on arrival against the bytes received. Completing carries
the whole file's SHA-256: a wrong one is refused at once (422), a right one claims the upload (202) and the
`finalize_upload` job re-reads the stored parts, hashing as it streams. A part sent under the wrong number
passes the per-part check (the sender hashed the same wrong bytes) but not the whole-file check, so the job
closes the upload as failed (`sha256_mismatch`) and no document is made. Its parts are left to the sweep.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.storage import CaseFileStorage, upload_prefix
from tests.support.db import ClientFor
from tests.support.files import MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import complete, put, put_all, row, run_finalize, sha, start

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
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: CaseFileStorage
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
    assert row(admin, "select received, chunk_sha256 from app.uploads where id = %s", upload) == (
        [2],
        {"2": sha(other)},
    )


async def test_receiving_a_chunk_touches_only_its_own_upload(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=36)
    client = await client_for(team["expert"])
    first, second = await start(client, team["case"], file), await start(client, team["case"], file)
    assert (await put(client, first, 1, file.chunk(1), sha(file.chunk(1)))).status_code == 200
    assert row(admin, "select received, chunk_sha256 from app.uploads where id = %s", second) == ([], {})
    assert row(admin, "select received from app.uploads where id = %s", first) == ([1],)


async def test_completing_with_the_right_file_hash_claims_the_upload_and_the_job_makes_the_document(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=37)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    resp = await complete(client, upload, file.sha256())
    assert (resp.status_code, resp.json()) == (202, {"status": "finalizing"}), resp.text
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("finalizing", None)
    assert row(
        admin,
        "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)",
        team["case"],
    ) == (0,)
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    found = row(admin, "select status, document_id from app.uploads where id = %s", upload)
    assert found is not None and found[0] == "complete"
    assert row(
        admin,
        "select sha256, sha256_verified_at is not null, uploaded_by from app.documents where id = %s",
        found[1],
    ) == (file.sha256(), True, team["expert"])
    done = await complete(client, upload, file.sha256())
    assert done.status_code == 200, done.text
    assert done.json()["document_id"] == str(found[1])


async def test_completing_with_a_wrong_file_hash_is_refused_at_once_and_the_upload_stays_open_with_its_parts(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=38)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    resp = await complete(client, upload, "c" * 64)
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "file_hash_mismatch"
    assert row(admin, "select status, document_id, finalize_job_id from app.uploads where id = %s", upload) == (
        "open",
        None,
        None,
    )
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count
    assert row(
        admin,
        "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)",
        team["case"],
    ) == (0,)
    again = await complete(client, upload, file.sha256())  # the sender's mistake was not held against the upload
    assert again.status_code == 202, again.text
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(
        admin,
        "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)",
        team["case"],
    ) == (1,)


async def test_the_right_size_but_wrong_bytes_are_caught_by_the_whole_file_hash_in_the_job(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(24 * MB, seed=39)  # three whole chunks
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    for n in (1, 2):
        assert (await put(client, upload, n, file.chunk(n))).status_code == 200
    # Chunk 2's bytes under chunk 3's number: right size, and the hash matches the bytes sent.
    assert (await put(client, upload, 3, file.chunk(2))).status_code == 200
    resp = await complete(client, upload, file.sha256())
    assert resp.status_code == 202, resp.text
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(
        admin,
        "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)",
        team["case"],
    ) == (0,)
    assert row(admin, "select status, document_id, failure from app.uploads where id = %s", upload) == (
        "failed",
        None,
        "sha256_mismatch",
    )
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count  # the sweep's job
    again = await complete(client, upload, file.sha256())
    assert (again.status_code, again.json()["error"]["code"]) == (409, "upload_closed"), again.text


@pytest.mark.parametrize("body", [None, {}, {"sha256": "nope"}, {"sha256": "A" * 63}])
async def test_completing_without_a_well_formed_file_hash_is_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, body: dict[str, str] | None
) -> None:
    file = SyntheticFile(17 * MB, seed=40)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    url = f"/v1/uploads/{upload}/complete"
    resp = await (client.post(url) if body is None else client.post(url, json=body))
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
        assert (await put(client, upload, n, file.chunk(n))).status_code == 200
    resp = await complete(client, upload, file.sha256())
    assert resp.status_code == 409, resp.text
    assert resp.json()["error"]["code"] == "chunks_missing"
    assert resp.json()["error"]["missing"] == [2]
    assert row(admin, "select status, finalize_job_id from app.uploads where id = %s", upload) == ("open", None)


async def test_completing_twice_gives_one_job_then_the_same_document_without_a_second_one(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=42)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    first = await complete(client, upload, file.sha256())
    again = await complete(client, upload, file.sha256())  # while the job is queued: the same answer, no second job
    assert (first.status_code, again.status_code) == (202, 202)
    assert row(
        admin, "select count(*) from app.jobs where case_id = %s and kind = 'finalize_upload'", team["case"]
    ) == (1,)
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    done = await complete(client, upload, file.sha256())
    assert done.status_code == 200, done.text
    assert row(
        admin,
        "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)",
        team["case"],
    ) == (1,)
    assert row(admin, "select document_id from app.uploads where id = %s", upload) == (
        uuid.UUID(done.json()["document_id"]),
    )
