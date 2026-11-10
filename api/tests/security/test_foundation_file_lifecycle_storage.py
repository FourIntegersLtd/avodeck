"""AUDIT-12 (file lifecycle): what is in Storage, whether a changed object is caught, and what a dead upload leaves.

Every "refused" or "caught" cell has a control in the same test: the same file, untouched, uploads and downloads
as the exact bytes that went up. Objects are changed through the same Storage adapter the app uses (the stand-in
S3, moto); bytes are made up.

Not tested, on purpose: an anonymous (unsigned) HTTP GET to an object's URL. moto does not enforce bucket policy
or block-public-access, so a refusal or a success there would say nothing about the real bucket; whether the
production bucket is private is a setting to check in AWS, not something this suite can show.

Facts recorded here:
- An abandoned open upload lives until `expires_at` (7 days after it starts, migration 0007) and the daily sweep
  then expires it and deletes its parts, so a leftover part can sit in Storage for up to about 8 days. A cancelled
  or failed upload keeps its parts until the next daily sweep (up to about a day).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import date

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.housekeeping import expire_uploads
from app.core.routes.uploads import Upload
from app.core.storage import CaseFileStorage, upload_prefix
from tests.support.db import ClientFor
from tests.support.file_lifecycle_uploads import (
    TWO_PART_SIZE,
    Team,
    blob,
    document_path,
    documents_at,
    fetch,
    flip_byte,
    head,
    key_pattern,
    make_team,
    object_path,
    open_upload,
    parts,
    upload_state,
    upload_whole,
)
from tests.support.files import STORAGE_BUCKET, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import (
    complete,
    complete_and_finalize,
    hold,
    put,
    release,
    run_finalize,
    sha,
    worker_database,
)

pytestmark = pytest.mark.anyio

MARKER = b"SYNTHETIC-MARKER-0001"
SMALL = blob(31, 100)


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
def team(seed: Seed) -> Team:
    return make_team(seed)


async def finalized(client_for: ClientFor, team: Team, data: bytes, case: uuid.UUID | None = None) -> tuple[str, str]:
    """Uploads `data` as the named expert and finalizes it; returns (upload id, document id)."""
    owner = await client_for(team.expert)
    upload = await upload_whole(owner, case or team.case, data)
    return upload, await complete_and_finalize(owner, upload, sha(data))


# ---------------------------------------------------------------------------------------------------------
# S1 storage, S2 download integrity
# ---------------------------------------------------------------------------------------------------------


async def test_s1_every_stored_object_has_the_expected_key_is_encrypted_and_is_not_the_plaintext(
    client_for: ClientFor, team: Team, storage: CaseFileStorage
) -> None:
    data = MARKER + blob(32, TWO_PART_SIZE - len(MARKER))
    owner = await client_for(team.expert)
    upload = await upload_whole(owner, team.case, data)
    for stage in ("open", "complete"):
        if stage == "complete":
            await complete_and_finalize(owner, upload, sha(data))
        keys = await storage.list_recursive(f"cases/{team.case}")
        assert [k for k in keys] == [object_path(team.case, upload, 1), object_path(team.case, upload, 2)], stage
        pattern = key_pattern(team.case, upload)
        for key, plain in zip(keys, parts(data), strict=True):
            assert pattern.match(key), key
            assert head(key)["ServerSideEncryption"] == "AES256", key
            stored = await storage.get(key)
            assert stored != plain and MARKER not in stored and len(stored) > len(plain), key
            assert stored[: len(plain)] != plain  # not the plaintext with something appended either


async def test_s1_no_storage_location_or_link_reaches_any_response(
    client_for: ClientFor, team: Team, admin: psycopg.Connection
) -> None:
    owner = await client_for(team.expert)
    seen = []
    start = await owner.post(
        f"/v1/cases/{team.case}/uploads",
        json={"kind": "records", "filename": "f.bin", "size": len(SMALL), "sha256": sha(SMALL)},
    )
    upload = start.json()["upload_id"]
    seen += [start, await put(owner, upload, 1, SMALL), await owner.get(f"/v1/uploads/{upload}")]
    seen.append(await complete(owner, upload, sha(SMALL)))
    document = await complete_and_finalize(owner, upload, sha(SMALL))
    seen += [await owner.get(f"/v1/uploads/{upload}"), await complete(owner, upload, sha(SMALL))]
    other = await open_upload(owner, team.case, SMALL)
    seen.append(await owner.post(f"/v1/uploads/{other}/cancel"))
    download = await fetch(owner, document_path(team.case, document))
    assert download.delivered_whole_file and download.body == SMALL
    forbidden = ("amazonaws", "x-amz", "signature", "127.0.0.1", STORAGE_BUCKET, "storage_path", "cases/", "/uploads/")
    for resp in seen:
        assert resp.status_code in (200, 201, 202), resp.text
        text = (resp.text + str(dict(resp.headers))).lower()
        for needle in forbidden:
            assert needle not in text, needle
        assert "location" not in resp.headers
    text = str(dict(download.headers)).lower()
    assert not any(needle in text for needle in forbidden)
    fields = set(Upload.model_fields)
    assert set(seen[2].json()) == fields  # no field a later change could slip a path or link into unseen
    assert not [f for f in fields if any(w in f for w in ("path", "url", "link", "bucket", "key"))]


async def test_s2_the_owner_downloads_exactly_the_bytes_that_went_up_with_safe_headers(
    client_for: ClientFor, team: Team
) -> None:
    data = blob(33, TWO_PART_SIZE)
    _, document = await finalized(client_for, team, data)
    owner = await client_for(team.expert)
    got = await fetch(owner, document_path(team.case, document))
    assert got.delivered_whole_file and got.body == data
    cache = {p.strip() for p in got.headers["cache-control"].split(",")}
    assert {"private", "no-store"} <= cache
    assert got.headers["content-type"] == "application/octet-stream"
    assert got.headers["x-content-type-options"] == "nosniff"
    # (no Content-Disposition is sent: recorded as the S3 finding in test_foundation_file_lifecycle_validation.py)
    # the same document through another case's path, and for a person without the case: nothing
    assert (await fetch(owner, document_path(team.second_case, document))).status == 404
    stranger = await client_for(team.outsider)
    assert (await fetch(stranger, document_path(team.case, document))).status == 404


async def test_s2_a_document_id_of_another_case_is_not_found_through_this_cases_path(
    client_for: ClientFor, team: Team
) -> None:
    """The route checks access to the case in the path and then looks the document up in that case's bundle only."""
    _, own_document = await finalized(client_for, team, SMALL)
    theirs_data = blob(34, 100)
    _, theirs = await finalized(client_for, team, theirs_data, case=team.second_case)  # a document of case B
    expert = await client_for(team.expert)
    member = await client_for(team.assistant)  # a member of case A only
    # controls: each person reads what is theirs to read
    assert (await fetch(expert, document_path(team.second_case, theirs))).body == theirs_data
    assert (await fetch(member, document_path(team.case, own_document))).body == SMALL
    # the member asks for B's document through A: 404 (and the named expert, who may read both, gets 404 too)
    assert (await fetch(member, document_path(team.case, theirs))).status == 404
    assert (await fetch(expert, document_path(team.case, theirs))).status == 404
    assert (await fetch(member, document_path(team.second_case, theirs))).status == 404, "and B's own path"


# ---------------------------------------------------------------------------------------------------------
# U2 a changed object is caught
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("target", ["same_case", "other_case"])
async def test_u2_a_part_sealed_for_another_upload_and_copied_in_does_not_finalize(
    client_for: ClientFor,
    team: Team,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
    target: str,
) -> None:
    owner = await client_for(team.expert)
    donor = await upload_whole(owner, team.case, SMALL)  # A: the part is sealed to A's path
    case_b = team.case if target == "same_case" else team.second_case
    victim = await upload_whole(owner, case_b, SMALL)  # B announces the same file, so only the seal can tell
    await storage.put(object_path(case_b, victim, 1), await storage.get(object_path(team.case, donor, 1)))
    assert (await complete(owner, victim, sha(SMALL))).status_code == 202
    await run_finalize(worker_dsn, admin, victim, storage, crypto, finish=True)
    state = upload_state(admin, victim)
    assert (state["status"], state["failure"], state["document_id"]) == ("failed", "chunk_unreadable", None)
    assert documents_at(admin, case_b, victim) == 0
    # control: the donor, untouched, becomes a document with the exact bytes
    document = await complete_and_finalize(owner, donor, sha(SMALL))
    got = await fetch(owner, document_path(team.case, document))
    assert got.delivered_whole_file and got.body == SMALL


async def test_u2_two_parts_swapped_in_storage_before_finalize_do_not_become_a_document(
    client_for: ClientFor,
    team: Team,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    data = blob(34, TWO_PART_SIZE)
    owner = await client_for(team.expert)
    clean = await upload_whole(owner, team.case, data)
    swapped = await upload_whole(owner, team.case, data)
    one, two = object_path(team.case, swapped, 1), object_path(team.case, swapped, 2)
    first, second = await storage.get(one), await storage.get(two)
    await storage.put(one, second)
    await storage.put(two, first)
    assert (await complete(owner, swapped, sha(data))).status_code == 202
    await run_finalize(worker_dsn, admin, swapped, storage, crypto, finish=True)
    state = upload_state(admin, swapped)
    assert (state["status"], state["failure"], state["document_id"]) == ("failed", "chunk_unreadable", None)
    assert documents_at(admin, team.case, swapped) == 0
    document = await complete_and_finalize(owner, clean, sha(data))  # control
    assert (await fetch(owner, document_path(team.case, document))).body == data


async def test_u2_a_part_deleted_before_finalize_reopens_the_upload_and_can_be_sent_again(
    client_for: ClientFor,
    team: Team,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    data = blob(35, TWO_PART_SIZE)
    owner = await client_for(team.expert)
    upload = await upload_whole(owner, team.case, data)
    assert (await complete(owner, upload, sha(data))).status_code == 202
    await storage.delete([object_path(team.case, upload, 2)])
    await run_finalize(worker_dsn, admin, upload, storage, crypto, finish=True)
    state = upload_state(admin, upload)
    assert (state["status"], state["received"], state["document_id"]) == ("open", [1], None)
    assert (await owner.get(f"/v1/uploads/{upload}")).json()["missing"] == [2]
    assert documents_at(admin, team.case, upload) == 0
    # control: sending the lost part again finishes the same upload with the exact bytes
    assert (await put(owner, upload, 2, parts(data)[1])).status_code == 200
    document = await complete_and_finalize(owner, upload, sha(data))
    assert (await fetch(owner, document_path(team.case, document))).body == data


@pytest.mark.parametrize("gone", ["first", "last", "all"])
async def test_u2_a_part_deleted_after_finalize_is_404_with_no_bytes_not_a_short_200(
    client_for: ClientFor, team: Team, storage: CaseFileStorage, gone: str
) -> None:
    data = blob(36, TWO_PART_SIZE)
    upload, document = await finalized(client_for, team, data)
    owner = await client_for(team.expert)
    path = document_path(team.case, document)
    whole = await fetch(owner, path)
    assert whole.delivered_whole_file and whole.body == data  # control, before the change
    victims = {"first": [1], "last": [2], "all": [1, 2]}[gone]
    await storage.delete([object_path(team.case, upload, n) for n in victims])
    got = await fetch(owner, path)
    assert got.error is None and got.status == 404
    assert len(got.body) < 500 and not data.startswith(got.body[:64])  # the small JSON error, not file bytes


@pytest.mark.parametrize("part", [1, 2])
async def test_u2_a_flipped_byte_in_a_stored_part_is_never_delivered_as_a_complete_file(
    client_for: ClientFor, team: Team, storage: CaseFileStorage, part: int
) -> None:
    data = blob(37, TWO_PART_SIZE)
    upload, document = await finalized(client_for, team, data)
    owner = await client_for(team.expert)
    path = document_path(team.case, document)
    assert (await fetch(owner, path)).body == data  # control
    key = object_path(team.case, upload, part)
    await storage.put(key, flip_byte(await storage.get(key)))
    got = await fetch(owner, path)
    assert not got.delivered_whole_file, got.status
    assert got.body != data
    assert got.error is not None  # a 5xx before any byte, or a stream cut off part way: never a clean 200


async def test_u2_a_part_re_sealed_with_the_right_key_but_other_bytes_is_caught_by_the_whole_file_hash(
    client_for: ClientFor, team: Team, storage: CaseFileStorage, crypto: CaseCrypto, worker_dsn: str
) -> None:
    """Only someone holding the case key and write access to Storage could make this; the hash still catches it."""
    upload, document = await finalized(client_for, team, SMALL)
    owner = await client_for(team.expert)
    path = document_path(team.case, document)
    assert (await fetch(owner, path)).body == SMALL  # control
    key = object_path(team.case, upload, 1)
    async with worker_database(worker_dsn) as database, database.job(team.case) as conn:
        data_key = await crypto.key(conn, team.case)
    forged = blob(38, len(SMALL))
    await storage.put(key, await crypto.seal_with(data_key, team.case, forged, purpose=key))
    got = await fetch(owner, path)
    # The bytes are sent first and the stream is then ended with an error (the whole-file check runs at the
    # end), so a client sees an aborted transfer, never a file that finished cleanly.
    assert not got.delivered_whole_file and got.error is not None


@pytest.mark.parametrize("name", ["00002", "00000", "zzz"])
async def test_u2_a_stray_object_under_a_documents_folder_is_never_streamed(
    client_for: ClientFor, team: Team, storage: CaseFileStorage, name: str
) -> None:
    upload, document = await finalized(client_for, team, SMALL)
    owner = await client_for(team.expert)
    path = document_path(team.case, document)
    assert (await fetch(owner, path)).body == SMALL  # control
    await storage.put(f"{upload_prefix(team.case, upload)}/{name}", b"stray object")
    got = await fetch(owner, path)
    assert got.status == 404 and b"stray object" not in got.body


async def test_u2_a_changed_part_hash_kept_on_an_open_upload_does_not_become_a_document(
    client_for: ClientFor,
    team: Team,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    owner = await client_for(team.expert)
    good = await upload_whole(owner, team.case, SMALL)
    bad = await upload_whole(owner, team.case, SMALL)
    # A person with database access (the admin role here) changes the hash kept for part 1. Migration 0125 freezes
    # the kept hashes once the upload leaves `open`, but allows the change while it is still open (parts may still
    # be arriving), so the update goes through and the check at finalize is what must catch it.
    admin.execute(
        "update app.uploads set chunk_sha256 = jsonb_build_object('1', %s::text) where id = %s", ("0" * 64, bad)
    )
    assert (await complete(owner, bad, sha(SMALL))).status_code == 202
    await run_finalize(worker_dsn, admin, bad, storage, crypto, finish=True)
    state = upload_state(admin, bad)
    assert (state["status"], state["failure"], state["document_id"]) == ("failed", "chunk_unreadable", None)
    assert documents_at(admin, team.case, bad) == 0
    document = await complete_and_finalize(owner, good, sha(SMALL))  # control
    assert (await fetch(owner, document_path(team.case, document))).body == SMALL


async def test_u2_a_changed_document_hash_stops_the_download_from_finishing_cleanly(
    client_for: ClientFor, team: Team, admin: psycopg.Connection
) -> None:
    _, document = await finalized(client_for, team, SMALL)
    owner = await client_for(team.expert)
    path = document_path(team.case, document)
    assert (await fetch(owner, path)).body == SMALL  # control
    # no database rule freezes `documents.sha256`, so the change goes through (a person with database access can do
    # it); what must hold is that the stream then fails its end-of-file check instead of finishing as a clean file
    admin.execute("update app.documents set sha256 = %s where id = %s", ("0" * 64, document))
    got = await fetch(owner, path)
    assert not got.delivered_whole_file


# ---------------------------------------------------------------------------------------------------------
# U4 what a dead upload leaves behind
# ---------------------------------------------------------------------------------------------------------


async def sweep(worker_dsn: str, storage: CaseFileStorage) -> None:
    async with worker_database(worker_dsn) as database, database.anonymous() as conn:
        await expire_uploads(conn, date.today(), storage)


async def controls(client_for: ClientFor, team: Team, data: bytes = SMALL) -> tuple[str, str, str, str]:
    """A finished document and an open upload with a part, in the same case: the sweep must leave both alone."""
    owner = await client_for(team.expert)
    done, document = await finalized(client_for, team, data)
    open_one = await upload_whole(owner, team.case, data)
    return done, document, open_one, str(team.case)


async def assert_controls_survive(
    client_for: ClientFor, team: Team, storage: CaseFileStorage, done: str, document: str, open_one: str
) -> None:
    owner = await client_for(team.expert)
    got = await fetch(owner, document_path(team.case, document))
    assert got.delivered_whole_file and got.body == SMALL
    assert await storage.list(upload_prefix(team.case, done)) == [object_path(team.case, done, 1)]
    assert await storage.list(upload_prefix(team.case, open_one)) == [object_path(team.case, open_one, 1)]


async def test_u4_a_cancelled_upload_keeps_its_parts_out_of_reach_until_the_sweep_then_none_are_left(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage, worker_dsn: str
) -> None:
    done, document, open_one, _ = await controls(client_for, team)
    owner = await client_for(team.expert)
    upload = await upload_whole(owner, team.case, SMALL)
    assert (await owner.post(f"/v1/uploads/{upload}/cancel")).status_code == 200
    prefix = upload_prefix(team.case, upload)
    assert await storage.list(prefix) == [object_path(team.case, upload, 1)]  # still stored: waits for the sweep
    # before the sweep: no endpoint serves the part (status is metadata only; no document exists to download)
    status = (await owner.get(f"/v1/uploads/{upload}")).json()
    assert status["status"] == "cancelled" and set(status) == set(Upload.model_fields)
    assert documents_at(admin, team.case, upload) == 0
    for guess in (upload, str(uuid.uuid4())):
        assert (await fetch(owner, document_path(team.case, guess))).status == 404
    await sweep(worker_dsn, storage)
    assert await storage.list_recursive(prefix) == [] and upload_state(admin, upload)["cleaned"] is True
    await assert_controls_survive(client_for, team, storage, done, document, open_one)


async def test_u4_a_failed_upload_keeps_its_parts_out_of_reach_until_the_sweep_then_none_are_left(
    client_for: ClientFor,
    team: Team,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    done, document, open_one, _ = await controls(client_for, team)
    owner = await client_for(team.expert)
    announced, sent = blob(39, 100), blob(40, 100)
    upload = await open_upload(owner, team.case, announced)
    assert (await put(owner, upload, 1, sent)).status_code == 200
    assert (await complete(owner, upload, sha(announced))).status_code == 202
    await run_finalize(worker_dsn, admin, upload, storage, crypto, finish=True)
    state = upload_state(admin, upload)
    assert (state["status"], state["failure"]) == ("failed", "sha256_mismatch")
    prefix = upload_prefix(team.case, upload)
    assert await storage.list(prefix) == [object_path(team.case, upload, 1)]
    status = (await owner.get(f"/v1/uploads/{upload}")).json()
    assert status["status"] == "failed" and status["document_id"] is None
    assert documents_at(admin, team.case, upload) == 0
    assert (await fetch(owner, document_path(team.case, upload))).status == 404
    await sweep(worker_dsn, storage)
    assert await storage.list_recursive(prefix) == [] and upload_state(admin, upload)["cleaned"] is True
    await assert_controls_survive(client_for, team, storage, done, document, open_one)


async def test_u4_an_expired_upload_leaves_no_parts_after_the_sweep(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage, worker_dsn: str
) -> None:
    done, document, open_one, _ = await controls(client_for, team)
    owner = await client_for(team.expert)
    upload = await upload_whole(owner, team.case, SMALL)
    admin.execute("update app.uploads set expires_at = now() - interval '1 hour' where id = %s", (upload,))
    prefix = upload_prefix(team.case, upload)
    assert len(await storage.list(prefix)) == 1  # before the sweep the part is still there
    await sweep(worker_dsn, storage)
    state = upload_state(admin, upload)
    assert state["status"] == "expired" and state["cleaned"] is True
    assert await storage.list_recursive(prefix) == []
    await assert_controls_survive(client_for, team, storage, done, document, open_one)


async def test_u4_a_part_that_lands_while_the_upload_is_cancelled_has_no_received_entry_and_is_swept(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage, worker_dsn: str
) -> None:
    """The real way to get an orphan part: `put_chunk` stores a new part before it takes the upload's lock,
    so a cancel between the two leaves an object that no `received` entry names (routes/uploads.py:274-285)."""
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, SMALL)
    prefix = upload_prefix(team.case, upload)
    hold(admin, upload)  # the part waits at the lock after it has been stored
    task = asyncio.ensure_future(put(owner, upload, 1, SMALL))
    try:
        # Not a fixed sleep: the part is stored by a request that then blocks at the lock held above, so Storage
        # is the only place the arrival shows; poll for it, bounded at 5 s, and fail loudly if it never comes.
        for _ in range(100):
            if await storage.list(prefix):
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError("the part was never stored")
        assert (await owner.post(f"/v1/uploads/{upload}/cancel")).status_code == 200
    finally:
        release(admin, upload)
    late = await task
    assert (late.status_code, late.json()["error"]["code"]) == (409, "upload_closed")
    state = upload_state(admin, upload)
    assert state["status"] == "cancelled" and state["received"] == []
    assert await storage.list(prefix) == [object_path(team.case, upload, 1)]  # the orphan: stored, not recorded
    await sweep(worker_dsn, storage)
    assert await storage.list_recursive(prefix) == [] and upload_state(admin, upload)["cleaned"] is True
