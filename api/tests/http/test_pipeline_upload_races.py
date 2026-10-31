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


async def put(client: httpx.AsyncClient, upload: str, n: int, data: bytes) -> httpx.Response:
    headers = {"content-type": "application/octet-stream", "x-chunk-sha256": sha(data)}
    return await client.put(f"/v1/uploads/{upload}/chunks/{n}", content=data, headers=headers)


async def put_all(client: httpx.AsyncClient, upload: str, file: SyntheticFile) -> None:
    for n, data in file.chunks():
        assert (await put(client, upload, n, data)).status_code == 200


def row(admin: psycopg.Connection, sql: str, *args: object) -> tuple[object, ...] | None:
    return admin.execute(sql, args).fetchone()  # type: ignore[arg-type]


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
            admin.execute(sql, (upload,))  # type: ignore[arg-type]
        admin.rollback()
    kept = row(
        admin, "select count(*) from jsonb_object_keys((select chunk_sha256 from app.uploads where id = %s))", upload
    )
    assert kept == (file.count,)


async def test_two_correct_completes_at_once_make_one_document_and_keep_its_parts(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    file = SyntheticFile(17 * MB, seed=47)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    body = {"sha256": file.sha256()}
    a, b = await asyncio.gather(
        one.post(f"/v1/uploads/{upload}/complete", json=body), two.post(f"/v1/uploads/{upload}/complete", json=body)
    )
    assert sorted([a.status_code, b.status_code]) == [200, 201], (a.text, b.text)
    assert a.json()["document_id"] == b.json()["document_id"]
    assert row(admin, "select count(*) from app.documents where case_id = %s", team["case"]) == (1,)
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count


async def test_a_wrong_hash_complete_racing_a_correct_one_never_deletes_a_registered_documents_parts(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    file = SyntheticFile(17 * MB, seed=48)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    good, bad = await asyncio.gather(
        one.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()}),
        two.post(f"/v1/uploads/{upload}/complete", json={"sha256": "c" * 64}),
    )
    parts = await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))
    documents = row(admin, "select count(*) from app.documents where case_id = %s", team["case"])
    if good.status_code == 201:  # the correct call won: the document and every part stay
        assert (bad.status_code, bad.json()["error"]["code"]) == (409, "file_hash_mismatch"), bad.text
        assert documents == (1,) and len(parts) == file.count
        assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)
    else:  # the wrong call won: the upload is closed, nothing registered, and the right one is told so
        assert (bad.status_code, bad.json()["error"]["code"]) == (422, "file_hash_mismatch"), bad.text
        assert (good.status_code, good.json()["error"]["code"]) == (409, "upload_closed"), good.text
        assert documents == (0,)
        assert row(admin, "select status from app.uploads where id = %s", upload) == ("failed",)


async def test_a_part_sent_while_the_upload_is_completing_never_leaves_a_document_that_disagrees_with_its_bytes(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    for round_ in range(3):
        file = SyntheticFile(17 * MB, seed=60 + round_)
        one, two = await client_for(team["expert"]), await client_for(team["expert"])
        upload = await start(one, team["case"], file)
        await put_all(one, upload, file)
        other = b"x" * len(file.chunk(2))
        done, late = await asyncio.gather(
            one.post(f"/v1/uploads/{upload}/complete", json={"sha256": file.sha256()}),
            put(two, upload, 2, other),
        )
        assert not (done.status_code == 201 and late.status_code == 200), (done.text, late.text)
        if done.status_code == 201:  # completing came first: the part was refused, the stored bytes are the file
            assert late.json()["error"]["code"] == "upload_closed"
            digest = hashlib.sha256()
            url = f"/v1/cases/{team['case']}/documents/{done.json()['document_id']}/file"
            async with one.stream("GET", url) as got:
                async for piece in got.aiter_bytes():
                    digest.update(piece)
            assert digest.hexdigest() == file.sha256()
            stored = row(admin, "select sha256 from app.documents where id = %s", done.json()["document_id"])
            assert stored == (file.sha256(),)
        else:  # the part came first: the bytes changed, so the whole-file check refused it and no document exists
            assert done.json()["error"]["code"] == "file_hash_mismatch", done.text
            assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("failed", None)
