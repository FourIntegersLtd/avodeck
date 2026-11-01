"""Uploads under contention and tampering (review of the upload integrity change; failure modes F30, F31, F43).

Two completes for the same upload at once must give one outcome and one job, not two. A wrong-hash call never
touches the stored parts or the upload's state. A part sent while the upload is finalizing is refused; one
that lands before the claim is covered by the job's whole-file hash, so the registered document's hash always
agrees with its stored bytes. A stored part that no longer matches the hash kept when it arrived fails the
upload in the job (`chunk_unreadable`). A finished upload's chunk data cannot be changed in the database.
Cleaning up the parts of a closed upload is the daily sweep's job, never a request's.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.routes import uploads
from app.core.storage import CaseFileStorage, upload_prefix
from tests.support.db import ClientFor
from tests.support.files import MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import (
    complete,
    hold,
    one_after_another,
    put,
    put_all,
    release,
    row,
    run_finalize,
    start,
)

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


def parts(admin: psycopg.Connection, case: uuid.UUID) -> int:
    found = row(admin, "select count(*) from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", case)
    assert found is not None
    count = found[0]
    assert isinstance(count, int)
    return count


async def test_a_repeat_complete_with_a_different_file_hash_is_refused_and_the_document_is_untouched(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=43)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    assert (await complete(client, upload, file.sha256())).status_code == 202
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    again = await complete(client, upload, "c" * 64)
    assert again.status_code == 409, again.text
    assert again.json()["error"]["code"] == "file_hash_mismatch"
    assert parts(admin, team["case"]) == 1
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count


async def test_a_repeat_complete_with_no_file_hash_is_still_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    file = SyntheticFile(17 * MB, seed=44)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    assert (await complete(client, upload, file.sha256())).status_code == 202
    again = await client.post(f"/v1/uploads/{upload}/complete")
    assert again.status_code == 422, again.text
    assert again.json()["error"]["code"] == "file_sha_missing"


async def test_a_stored_part_that_no_longer_matches_its_own_hash_fails_the_upload_naming_no_hash_and_is_audited(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=45)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    admin.execute(
        "update app.uploads set chunk_sha256 = jsonb_set(chunk_sha256, '{2}', to_jsonb(%s::text)) where id = %s",
        ("d" * 64, upload),
    )
    assert (await complete(client, upload, file.sha256())).status_code == 202
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert row(admin, "select status, document_id, failure from app.uploads where id = %s", upload) == (
        "failed",
        None,
        "chunk_unreadable",
    )
    assert parts(admin, team["case"]) == 0
    assert row(
        admin,
        "select count(*) from app.audit_log where target = %s and action = 'upload.chunk_unreadable'",
        f"upload:{upload}",
    ) == (1,)
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count  # left to expire
    got = await client.get(f"/v1/uploads/{upload}")
    assert got.json()["failure"] == "chunk_unreadable"
    assert "d" * 64 not in got.text


@pytest.mark.parametrize("finished", ["complete", "failed"])
async def test_the_database_refuses_changes_to_the_chunk_data_of_a_finished_upload(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    finished: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=46)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    if finished == "failed":  # a part whose kept hash no longer matches
        admin.execute(
            "update app.uploads set chunk_sha256 = jsonb_set(chunk_sha256, '{1}', to_jsonb(%s::text)) where id = %s",
            ("d" * 64, upload),
        )
    assert (await complete(client, upload, file.sha256())).status_code == 202
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
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


async def test_two_correct_completes_at_once_make_one_job_one_document_and_keep_its_parts(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=47)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    first, second = await one_after_another(
        admin, upload, [complete(one, upload, file.sha256()), complete(two, upload, file.sha256())]
    )
    assert (first.status_code, second.status_code) == (202, 202), (first.text, second.text)
    assert row(
        admin, "select count(*) from app.jobs where case_id = %s and kind = 'finalize_upload'", team["case"]
    ) == (1,)
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    third = await complete(one, upload, file.sha256())
    assert third.status_code == 200, third.text
    assert parts(admin, team["case"]) == 1
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count


async def test_a_wrong_hash_complete_after_a_correct_one_leaves_the_upload_finalizing_and_the_document_comes(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=48)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    good, bad = await one_after_another(
        admin, upload, [complete(one, upload, file.sha256()), complete(two, upload, "c" * 64)]
    )
    assert good.status_code == 202, good.text
    assert bad.status_code == 202, bad.text  # finalizing: the claim stands whatever the later call sent
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert parts(admin, team["case"]) == 1
    assert row(admin, "select status from app.uploads where id = %s", upload) == ("complete",)
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count


async def test_a_correct_complete_after_a_wrong_hash_one_still_succeeds_and_the_parts_are_kept(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=49)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    bad, good = await one_after_another(
        admin, upload, [complete(one, upload, "c" * 64), complete(two, upload, file.sha256())]
    )
    assert (bad.status_code, bad.json()["error"]["code"]) == (422, "file_hash_mismatch"), bad.text
    assert good.status_code == 202, good.text
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert parts(admin, team["case"]) == 1


async def test_a_part_queued_behind_completing_is_refused_and_the_document_matches_its_bytes(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    file = SyntheticFile(17 * MB, seed=50)
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    upload = await start(one, team["case"], file)
    await put_all(one, upload, file)
    other = b"x" * len(file.chunk(2))
    done, late = await one_after_another(
        admin, upload, [complete(one, upload, file.sha256()), put(two, upload, 2, other)]
    )
    assert done.status_code == 202, done.text
    assert (late.status_code, late.json()["error"]["code"]) == (409, "upload_closed"), late.text
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    finished = await complete(one, upload, file.sha256())
    digest = hashlib.sha256()
    async with one.stream("GET", f"/v1/cases/{team['case']}/documents/{finished.json()['document_id']}/file") as got:
        async for piece in got.aiter_bytes():
            digest.update(piece)
    assert digest.hexdigest() == file.sha256()
    assert row(admin, "select sha256 from app.documents where id = %s", finished.json()["document_id"]) == (
        file.sha256(),
    )


async def test_a_part_that_lands_before_the_claim_makes_the_job_fail_on_the_whole_file_hash(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
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
    assert done.status_code == 202, done.text
    await run_finalize(worker_dsn, admin, upload, storage, crypto)
    assert parts(admin, team["case"]) == 0
    assert row(admin, "select status, document_id, failure from app.uploads where id = %s", upload) == (
        "failed",
        None,
        "sha256_mismatch",
    )
    assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == file.count


async def test_failing_completes_at_once_do_not_exhaust_the_connection_pool(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    """The shared test pool holds two connections: a failing call that needed a second one would starve."""
    one, two = await client_for(team["expert"]), await client_for(team["expert"])
    files = [SyntheticFile(17 * MB, seed=52), SyntheticFile(17 * MB, seed=53)]
    uploads_ = [await start(one, team["case"], f) for f in files]
    for upload, file in zip(uploads_, files, strict=True):
        await put_all(one, upload, file)
    first, second = await asyncio.wait_for(
        asyncio.gather(complete(one, uploads_[0], "c" * 64), complete(two, uploads_[1], "c" * 64)), timeout=20
    )
    assert [r.json()["error"]["code"] for r in (first, second)] == ["file_hash_mismatch"] * 2
    for upload in uploads_:
        assert row(admin, "select status from app.uploads where id = %s", upload) == ("open",)
        assert len(await storage.list(upload_prefix(team["case"], uuid.UUID(upload)))) == 3


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
    assert row(admin, "select status, finalize_job_id from app.uploads where id = %s", upload) == ("open", None)
    again = await complete(client, upload, file.sha256())  # nothing was left half-done
    assert again.status_code == 202, again.text


async def test_an_upload_expired_by_the_sweep_before_completing_is_reported_closed_not_a_server_error(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    file = SyntheticFile(17 * MB, seed=55)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    admin.execute("update app.uploads set status = 'expired' where id = %s", (upload,))  # the daily sweep
    resp = await complete(client, upload, file.sha256())
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "upload_closed"), resp.text
    assert parts(admin, team["case"]) == 0
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("expired", None)


async def test_an_upload_expired_by_the_sweep_part_way_through_the_job_makes_no_document_and_no_error(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    file = SyntheticFile(17 * MB, seed=56)
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], file)
    await put_all(client, upload, file)
    assert (await complete(client, upload, file.sha256())).status_code == 202
    real_get = storage.get
    swept = False

    async def sweeping(path: str) -> bytes:
        nonlocal swept
        if not swept:
            swept = True
            admin.execute("update app.uploads set status = 'expired' where id = %s", (upload,))  # the daily sweep
        return await real_get(path)

    monkeypatch.setattr(storage, "get", sweeping)
    await run_finalize(worker_dsn, admin, upload, storage, crypto)  # does not raise
    assert parts(admin, team["case"]) == 0
    assert row(admin, "select status, document_id from app.uploads where id = %s", upload) == ("expired", None)
