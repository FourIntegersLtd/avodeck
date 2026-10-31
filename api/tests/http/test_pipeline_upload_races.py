"""Uploads under contention and tampering (review of the upload integrity change; failure modes F30, F31).

Two calls for the same upload at once must give one outcome, not two. A wrong-hash call that loses the race
never deletes the parts of a document another call registered. A part sent while the upload is completing
either lands before the whole-file hash is taken or is refused: the registered document's hash always
agrees with its stored bytes. A stored part that no longer matches the hash kept when it arrived closes the
upload. A finished upload's chunk data cannot be changed in the database.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator, Awaitable

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.routes import uploads
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


async def put(client: httpx.AsyncClient, upload: str, n: int, data: bytes) -> httpx.Response:
    headers = {"content-type": "application/octet-stream", "x-chunk-sha256": sha(data)}
    return await client.put(f"/v1/uploads/{upload}/chunks/{n}", content=data, headers=headers)


async def put_all(client: httpx.AsyncClient, upload: str, file: SyntheticFile) -> None:
    for n, data in file.chunks():
        assert (await put(client, upload, n, data)).status_code == 200


def row(admin: psycopg.Connection, sql: str, *args: object) -> tuple[object, ...] | None:
    return admin.execute(sql, args).fetchone()


async def test_a_repeat_complete_with_a_different_file_hash_is_refused_and_the_document_is_untouched(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    file = SyntheticFile(17 * MB, seed=43)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    first = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})
    assert first.status_code == 201, first.text
    again = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": "c" * 64})
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "file_hash_mismatch"
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (1,)
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == (
        "complete",
        uuid.UUID(first.json()["document_id"]),
    )
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count


async def test_a_repeat_complete_with_no_file_hash_is_still_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    file = SyntheticFile(17 * MB, seed=44)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    assert (await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})).status_code == 201
    again = await client.post(f"/v1/uploads/{upload}/complete")
    assert again.status_code == 422, again.text
    assert again.json()["error"]["code"] == "file_sha_missing"


async def test_a_stored_part_that_no_longer_matches_its_own_hash_closes_the_upload_naming_only_its_number(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=45)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    admin.execute(
        "update app.uploads set chunk_sha256 = jsonb_set(chunk_sha256, '{2}', to_jsonb(%s::text)) where id = %s",
        ("d" * 64, upload),
    )
    resp = await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()})
    assert resp.status_code == 422, resp.text
    body = resp.json()["error"]
    assert body["code"] == "chunk_hash_mismatch"
    assert body["part"] == 2
    assert "d" * 64 not in resp.text
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("failed", None)
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (0,)


@pytest.mark.parametrize("finished", ["complete", "failed"])
async def test_the_database_refuses_changes_to_the_chunk_data_of_a_finished_upload(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, finished: str
) -> None:
    file = SyntheticFile(17 * MB, seed=46)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    sent = file.sha256() if finished == "complete" else "c" * 64
    await client.post(f"/v1/uploads/{upload}/complete", json={"sha256": sent})
    assert row(admin, "select status from app.uploads where id = %s", upload) == (finished,)
    for sql in (
        "update app.uploads set chunk_sha256 = '{}'::jsonb where id = %s",
        "update app.uploads set chunk_sha256 = jsonb_set(chunk_sha256, '{1}', to_jsonb('e'::text)) where id = %s",
        "update app.uploads set received = '{}' where id = %s",
    ):
        with pytest.raises(psycopg.Error):
            admin.execute(sql, (upload,))
        admin.rollback()
    kept = row(
        admin, "select count(*) from jsonb_object_keys((select chunk_sha256 from app.uploads where id = %s))", upload
    )
    assert kept == (file.count,)


LOCK = "pg_advisory_{}lock(hashtextextended(%s::text, 0))"


def hold(admin: psycopg.Connection, upload: str) -> None:
    """Take the upload's lock from outside, so calls pile up behind it in a known order."""
    admin.execute(f"select {LOCK.format('')}", (upload,))


def release(admin: psycopg.Connection, upload: str) -> None:
    admin.execute(f"select {LOCK.format('un')}", (upload,))


async def waiters(admin: psycopg.Connection, count: int) -> None:
    """Wait until `count` calls are queued on an advisory lock."""
    for _ in range(400):
        queued = row(admin, "select count(*) from pg_locks where locktype = 'advisory' and not granted")
        if queued is not None and queued[0] >= count:  # type: ignore[operator]
            return
        await asyncio.sleep(0.05)
    raise AssertionError("the calls never queued on the upload's lock")


async def one_after_another(
    admin: psycopg.Connection, upload: str, calls: list[Awaitable[httpx.Response]]
) -> list[httpx.Response]:
    """Start the calls one at a time behind a held lock (each queued before the next starts), then let go."""
    hold(admin, upload)
    tasks = []
    try:
        for queued, call in enumerate(calls, start=1):
            tasks.append(asyncio.ensure_future(call))
            await waiters(admin, queued)
    finally:
        release(admin, upload)
    return list(await asyncio.gather(*tasks))


def complete(client: httpx.AsyncClient, upload: str, digest: str) -> Awaitable[httpx.Response]:
    return client.post(f"/v1/uploads/{upload}/complete", json={"sha256": digest})


async def test_two_correct_completes_at_once_make_one_document_and_keep_its_parts(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    file = SyntheticFile(17 * MB, seed=47)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    first, second = await one_after_another(
        admin, upload, [complete(one, upload, file.sha256()), complete(two, upload, file.sha256())]
    )
    assert (first.status_code, second.status_code) == (201, 200), (first.text, second.text)
    assert first.json()["document_id"] == second.json()["document_id"]
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (1,)
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count


async def test_a_wrong_hash_complete_after_a_correct_one_is_refused_and_keeps_the_document_and_its_parts(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    file = SyntheticFile(17 * MB, seed=48)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    good, bad = await one_after_another(
        admin, upload, [complete(one, upload, file.sha256()), complete(two, upload, "c" * 64)]
    )
    assert good.status_code == 201, good.text
    assert (bad.status_code, bad.json()["error"]["code"]) == (409, "file_hash_mismatch"), bad.text
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (1,)
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count


async def test_a_correct_complete_after_a_wrong_hash_one_finds_the_upload_closed_and_its_parts_deleted(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    file = SyntheticFile(17 * MB, seed=49)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    bad, good = await one_after_another(
        admin, upload, [complete(one, upload, "c" * 64), complete(two, upload, file.sha256())]
    )
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "file_hash_mismatch"), bad.text
    assert (good.status_code, good.json()["error"]["code"]) == (409, "upload_closed"), good.text
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (0,)
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("failed", None)
    assert await storage.list(upload_prefix(team["case"], uuid.UUID(upload))) == []


async def test_a_part_queued_behind_completing_is_refused_and_the_document_matches_its_bytes(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=50)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    other = b"x" * len(file.chunk(2))
    done, late = await one_after_another(
        admin, upload, [complete(one, upload, file.sha256()), put(two, upload, 2, other)]
    )
    assert done.status_code == 201, done.text
    assert (late.status_code, late.json()["error"]["code"]) == (409, "upload_closed"), late.text
    digest = hashlib.sha256()
    async with one.stream("GET", f"/v1/cases/{team['case']}/documents/{done.json()['document_id']}/file") as got:
        async for piece in got.aiter_bytes():
            digest.update(piece)
    assert digest.hexdigest() == file.sha256()
    assert row(admin, "select sha256 from app.documents where id = %s", done.json()["document_id"]) == (file.sha256(),)


async def test_a_part_that_lands_before_the_whole_file_hash_is_taken_makes_the_complete_fail(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    file = SyntheticFile(17 * MB, seed=51)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    other = b"x" * len(file.chunk(2))
    late, done = await one_after_another(
        admin, upload, [put(two, upload, 2, other), complete(one, upload, file.sha256())]
    )
    assert late.status_code == 200, late.text
    assert (done.status_code, done.json()["error"]["code"]) == (422, "file_hash_mismatch"), done.text
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (0,)
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("failed", None)
    assert await storage.list(upload_prefix(team["case"], uuid.UUID(upload))) == []


async def test_failing_completes_at_once_do_not_exhaust_the_connection_pool(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    """The shared test pool holds two connections: a failing call that needed a second one would starve."""
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    files = [SyntheticFile(17 * MB, seed=52), SyntheticFile(17 * MB, seed=53)]
    uploads = [await start(one, team["case"], f) for f in files]
    for upload, file in zip(uploads, files, strict=True):
        await put_all(one, upload, file)
    first, second = await asyncio.wait_for(
        asyncio.gather(complete(one, uploads[0], "c" * 64), complete(two, uploads[1], "c" * 64)), timeout=20
    )
    assert [r.json()["error"]["code"] for r in (first, second)] == ["file_hash_mismatch"] * 2
    for upload in uploads:
        assert row(admin, "select status from app.uploads where id = %s", upload) == ("failed",)
        assert await storage.list(upload_prefix(team["case"], uuid.UUID(upload))) == []


async def test_a_call_waits_only_a_few_seconds_for_the_uploads_lock_then_says_it_is_busy(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    file = SyntheticFile(17 * MB, seed=54)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    monkeypatch.setattr(uploads, "LOCK_WAIT_MS", 300)
    hold(admin, upload)
    try:
        finish = await complete(client, upload, file.sha256())
        part = await put(client, upload, 2, file.chunk(2))
    finally:
        release(admin, upload)
    for resp in (finish, part):
        assert (resp.status_code, resp.json()["error"]["code"]) == (409, "upload_busy"), resp.text
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("open",)
    again = await complete(client, upload, file.sha256())  # nothing was left half-done
    assert again.status_code == 201, again.text


async def test_an_upload_expired_by_the_sweep_part_way_through_completing_is_reported_closed_not_a_server_error(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = SyntheticFile(17 * MB, seed=55)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    reading, go = asyncio.Event(), asyncio.Event()
    real_get = storage.get

    async def gated(path: str) -> bytes:
        reading.set()
        await go.wait()
        return await real_get(path)

    monkeypatch.setattr(storage, "get", gated)
    call = asyncio.ensure_future(complete(client, upload, file.sha256()))
    await reading.wait()
    admin.execute("update app.uploads set status = 'expired' where id = %s", (upload,))  # the daily sweep
    go.set()
    resp = await call
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "upload_closed"), resp.text
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (0,)
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("expired", None)
