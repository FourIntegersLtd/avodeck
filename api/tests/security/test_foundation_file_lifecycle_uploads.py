"""AUDIT-12 (file lifecycle): who may touch an upload, what a part must look like, and what an upload may carry.

Each test asserts the safe behaviour and pairs every "refused" with the rightful owner succeeding on the same
kind of request against a real stored upload, so a 404 for everyone cannot pass. Bytes are made up.

Facts recorded here rather than asserted (so they do not look like gaps):
- `docs/spec/permissions.md` gives `case.upload` to the named expert and the assistant, and defines it as
  "starting, sending parts of, completing, cancelling and downloading an upload"; it does not tie an upload to
  the person who started it (U1b below).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.storage import CaseFileStorage, upload_prefix
from tests.support.db import ClientFor
from tests.support.file_lifecycle_uploads import (
    TWO_PART_SIZE,
    Team,
    begin,
    blob,
    document_path,
    documents_at,
    fetch,
    key_pattern,
    make_team,
    object_path,
    open_upload,
    send_all,
    upload_state,
    upload_whole,
)
from tests.support.files import CHUNK, MB, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import complete, complete_and_finalize, put, record_clean_scan, run_finalize, sha

pytestmark = pytest.mark.anyio

SMALL = blob(11, 100)


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


def rows_for(admin: psycopg.Connection, case: uuid.UUID) -> int:
    found = admin.execute("select count(*) from app.uploads where case_id = %s", (case,)).fetchone()
    assert found is not None
    return int(found[0])


# ---------------------------------------------------------------------------------------------------------
# U1 authorisation
# ---------------------------------------------------------------------------------------------------------


async def test_u1_signed_out_calls_are_401_and_the_owner_gets_through(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, SMALL)
    anon = await client_for(None)

    refused = {
        "start": await begin(anon, team.case, SMALL),
        "chunk": await put(anon, upload, 1, SMALL),
        "status": await anon.get(f"/v1/uploads/{upload}"),
        "complete": await complete(anon, upload, sha(SMALL)),
        "cancel": await anon.post(f"/v1/uploads/{upload}/cancel"),
    }
    assert {name: r.status_code for name, r in refused.items()} == dict.fromkeys(refused, 401)
    # nothing happened: no part stored, no row added, still open
    assert await storage.list(upload_prefix(team.case, upload)) == []
    assert upload_state(admin, upload)["status"] == "open" and upload_state(admin, upload)["received"] == []
    assert rows_for(admin, team.case) == 1

    # positive control: the same five calls as the owner succeed on the same upload
    assert (await owner.get(f"/v1/uploads/{upload}")).status_code == 200
    assert (await put(owner, upload, 1, SMALL)).status_code == 200
    document = await complete_and_finalize(owner, upload, sha(SMALL), scanned=True)
    assert documents_at(admin, team.case, upload) == 1
    assert (await anon.get(document_path(team.case, document))).status_code == 401
    assert (await owner.get(document_path(team.case, document))).status_code == 200
    other = await open_upload(owner, team.case, SMALL)
    assert (await owner.post(f"/v1/uploads/{other}/cancel")).status_code == 200


async def test_u1_a_forged_upload_id_is_404_everywhere_and_a_real_one_is_not(client_for: ClientFor, team: Team) -> None:
    owner = await client_for(team.expert)
    real = await open_upload(owner, team.case, SMALL)
    for forged in (str(uuid.uuid4()), str(uuid.UUID(int=0)), real[:-1] + ("0" if real[-1] != "0" else "1")):
        assert (await owner.get(f"/v1/uploads/{forged}")).status_code == 404
        assert (await put(owner, forged, 1, SMALL)).status_code == 404
        assert (await complete(owner, forged, sha(SMALL))).status_code == 404
        assert (await owner.post(f"/v1/uploads/{forged}/cancel")).status_code == 404
    assert (await owner.get("/v1/uploads/not-a-uuid")).status_code == 422
    # the real id answers every call
    assert (await owner.get(f"/v1/uploads/{real}")).status_code == 200
    assert (await put(owner, real, 1, SMALL)).status_code == 200
    assert (await complete(owner, real, sha(SMALL))).status_code == 202


async def test_u1_a_person_with_no_part_in_the_case_gets_404_on_someone_elses_upload(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    owner = await client_for(team.expert)
    outsider = await client_for(team.outsider)
    upload = await upload_whole(owner, team.case, SMALL)
    before = await storage.get(object_path(team.case, upload, 1))

    refused = [
        await begin(outsider, team.case, SMALL),
        await outsider.get(f"/v1/uploads/{upload}"),
        await put(outsider, upload, 1, blob(12, 100)),
        await complete(outsider, upload, sha(SMALL)),
        await outsider.post(f"/v1/uploads/{upload}/cancel"),
    ]
    assert [r.status_code for r in refused] == [404] * 5
    assert upload_state(admin, upload)["status"] == "open" and upload_state(admin, upload)["received"] == [1]
    assert await storage.get(object_path(team.case, upload, 1)) == before  # the stranger's bytes did not land
    assert rows_for(admin, team.case) == 1

    document = await complete_and_finalize(owner, upload, sha(SMALL), scanned=True)  # the owner is not locked out
    assert (await outsider.get(document_path(team.case, document))).status_code == 404
    assert (await owner.get(document_path(team.case, document))).status_code == 200


async def test_u1_case_roles_and_org_roles_without_case_upload_are_refused_and_the_assistant_is_not(
    client_for: ClientFor, team: Team, admin: psycopg.Connection
) -> None:
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, SMALL)
    start_before = rows_for(admin, team.case)
    for name in ("viewer", "supervisor", "co_expert", "org_admin"):
        client = await client_for(getattr(team, name))
        assert (await begin(client, team.case, SMALL)).status_code == 403, name
        # row-level security hides another role's upload row, so the upload id answers 404 (403 is as safe)
        assert (await client.get(f"/v1/uploads/{upload}")).status_code in (403, 404), name
        assert (await put(client, upload, 1, SMALL)).status_code in (403, 404), name
        assert (await complete(client, upload, sha(SMALL))).status_code in (403, 404), name
        assert (await client.post(f"/v1/uploads/{upload}/cancel")).status_code in (403, 404), name
    assert rows_for(admin, team.case) == start_before
    assert upload_state(admin, upload)["received"] == []
    # positive control: the assistant (case.upload in permissions.md) starts one
    assistant = await client_for(team.assistant)
    assert (await begin(assistant, team.case, SMALL)).status_code == 201
    assert rows_for(admin, team.case) == start_before + 1


async def test_u1_a_member_removed_mid_upload_is_refused_on_the_next_chunk_and_on_complete(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    data = blob(13, TWO_PART_SIZE)
    assistant = await client_for(team.assistant)
    # control: while still a member, the assistant gets a whole upload through to a document
    done = await upload_whole(assistant, team.case, SMALL)
    await complete_and_finalize(assistant, done, sha(SMALL))
    assert documents_at(admin, team.case, done) == 1

    upload = await open_upload(assistant, team.case, data)
    assert (await put(assistant, upload, 1, data[:CHUNK])).status_code == 200
    admin.execute(
        "update app.case_members set removed_at = now() where case_id = %s and person_id = %s and removed_at is null",
        (team.case, team.assistant),
    )
    last = data[CHUNK:]
    assert (await put(assistant, upload, 2, last)).status_code == 404
    assert (await complete(assistant, upload, sha(data))).status_code == 404
    assert (await assistant.get(f"/v1/uploads/{upload}")).status_code == 404
    assert (await assistant.post(f"/v1/uploads/{upload}/cancel")).status_code == 404
    assert (await begin(assistant, team.case, SMALL)).status_code == 404
    state = upload_state(admin, upload)
    assert state["status"] == "open" and state["received"] == [1] and state["document_id"] is None
    assert await storage.list(upload_prefix(team.case, upload)) == [object_path(team.case, upload, 1)]
    assert documents_at(admin, team.case, upload) == 0


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 POLICY U1b: any case member with case.upload (the assistant) can send a part into, "
    "complete or cancel a colleague's open upload; nothing ties an upload to its starter: "
    "api/app/core/routes/uploads.py:161 (_load checks the case action only; permissions.md case.upload "
    "covers 'sending parts of, completing, cancelling' without an owner)",
)
async def test_u1b_a_colleague_cannot_write_into_cancel_or_complete_another_members_upload(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    owner = await client_for(team.expert)
    colleague = await client_for(team.assistant)
    upload = await open_upload(owner, team.case, SMALL)
    attempts = {"chunk": (await put(colleague, upload, 1, blob(14, 100))).status_code}
    other = await upload_whole(owner, team.case, SMALL)
    attempts["complete"] = (await complete(colleague, other, sha(SMALL))).status_code
    third = await open_upload(owner, team.case, SMALL)
    attempts["cancel"] = (await colleague.post(f"/v1/uploads/{third}/cancel")).status_code
    assert all(code in (403, 404) for code in attempts.values()), attempts
    # control: the starter's own calls work on the same uploads
    assert (await put(owner, upload, 1, SMALL)).status_code == 200
    assert upload_state(admin, other)["status"] == "open"


# ---------------------------------------------------------------------------------------------------------
# U2 integrity at the request
# ---------------------------------------------------------------------------------------------------------


async def test_u2_a_wrong_or_missing_part_checksum_is_refused_and_stores_nothing(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, SMALL)
    prefix = upload_prefix(team.case, upload)
    wrong = await put(owner, upload, 1, SMALL, digest=sha(b"another part"))
    assert (wrong.status_code, wrong.json()["error"]["code"]) == (422, "chunk_corrupted")
    missing = await put(owner, upload, 1, SMALL, digest=None)
    assert (missing.status_code, missing.json()["error"]["code"]) == (422, "chunk_sha_missing")
    for bad in ("", "zz", "0" * 63, "0" * 65, sha(SMALL) + " "):
        resp = await put(owner, upload, 1, SMALL, digest=bad)
        assert resp.status_code == 422, bad
    assert await storage.list(prefix) == [] and upload_state(admin, upload)["received"] == []
    ok = await put(owner, upload, 1, SMALL)  # positive control
    assert ok.status_code == 200 and await storage.list(prefix) == [object_path(team.case, upload, 1)]


async def test_u2_a_part_of_the_wrong_length_is_refused_and_stores_nothing(
    client_for: ClientFor, team: Team, storage: CaseFileStorage
) -> None:
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, SMALL)  # one part of exactly 100 bytes
    prefix = upload_prefix(team.case, upload)
    short, long_, empty = SMALL[:-1], SMALL + b"x", b""
    assert (await put(owner, upload, 1, short)).status_code == 422
    assert (await put(owner, upload, 1, empty)).status_code == 422
    assert (await put(owner, upload, 1, long_)).status_code == 413
    assert (await put(owner, upload, 1, blob(15, CHUNK + 1))).status_code == 413  # more than a whole part
    # a Content-Length that understates the body does not let extra bytes through
    lying = await owner.put(
        f"/v1/uploads/{upload}/chunks/1",
        content=long_,
        headers={"content-length": "5", "x-chunk-sha256": sha(long_), "content-type": "application/octet-stream"},
    )
    assert lying.status_code == 413
    # one that overstates it is refused before the body is read
    big = await owner.put(
        f"/v1/uploads/{upload}/chunks/1",
        content=SMALL,
        headers={
            "content-length": str(10 * MB),
            "x-chunk-sha256": sha(SMALL),
            "content-type": "application/octet-stream",
        },
    )
    assert big.status_code == 413
    assert await storage.list(prefix) == []
    assert (await put(owner, upload, 1, SMALL)).status_code == 200  # control
    assert len(await storage.list(prefix)) == 1


async def test_u2_the_last_part_must_be_exactly_the_remainder(
    client_for: ClientFor, team: Team, storage: CaseFileStorage
) -> None:
    data = blob(16, TWO_PART_SIZE)
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, data)
    assert (await put(owner, upload, 2, data[CHUNK:] + b"y")).status_code == 413
    assert (await put(owner, upload, 2, data[CHUNK:][:-1])).status_code == 422
    assert (await put(owner, upload, 1, data[CHUNK:])).status_code in (413, 422)  # part 2's bytes in slot 1
    assert await storage.list(upload_prefix(team.case, upload)) == []
    assert (await put(owner, upload, 2, data[CHUNK:])).status_code == 200


@pytest.mark.parametrize("n", ["0", "2", "-1", "abc", "1.5", "99999999999999999999", "٣"])
async def test_u2_part_numbers_outside_the_file_are_refused(
    client_for: ClientFor, team: Team, storage: CaseFileStorage, n: str
) -> None:
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, SMALL)  # one part: only 1 is in the file
    resp = await owner.put(
        f"/v1/uploads/{upload}/chunks/{n}",
        content=SMALL,
        headers={"x-chunk-sha256": sha(SMALL), "content-type": "application/octet-stream"},
    )
    assert resp.status_code == 422, resp.text
    assert await storage.list(upload_prefix(team.case, upload)) == []
    assert (await put(owner, upload, 1, SMALL)).status_code == 200  # control


async def test_u2_complete_needs_every_part_and_the_checksum_sent_at_the_start(
    client_for: ClientFor, team: Team, admin: psycopg.Connection
) -> None:
    data = blob(17, TWO_PART_SIZE)
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, data)
    assert (await put(owner, upload, 1, data[:CHUNK])).status_code == 200
    short = await complete(owner, upload, sha(data))
    assert (short.status_code, short.json()["error"]["code"]) == (409, "chunks_missing")
    assert upload_state(admin, upload)["status"] == "open"
    assert (await put(owner, upload, 2, data[CHUNK:])).status_code == 200
    for bad in (sha(b"another file"), "", "xyz", "0" * 63):
        resp = await complete(owner, upload, bad)
        assert resp.status_code == 422, bad
    assert (await owner.post(f"/v1/uploads/{upload}/complete", json={})).status_code == 422
    assert (await owner.post(f"/v1/uploads/{upload}/complete")).status_code == 422
    assert upload_state(admin, upload)["status"] == "open"
    assert (await complete(owner, upload, sha(data).upper())).status_code == 202  # control (case is folded)
    assert upload_state(admin, upload)["status"] == "finalizing"


# ---------------------------------------------------------------------------------------------------------
# U3 state
# ---------------------------------------------------------------------------------------------------------


async def test_u3_a_part_after_complete_is_refused_and_the_stored_object_is_unchanged(
    client_for: ClientFor,
    team: Team,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    owner = await client_for(team.expert)
    upload = await upload_whole(owner, team.case, SMALL)
    path = object_path(team.case, upload, 1)
    sealed = await storage.get(path)
    assert (await complete(owner, upload, sha(SMALL))).status_code == 202  # finalizing
    for replacement in (blob(18, 100), SMALL):
        resp = await put(owner, upload, 1, replacement)
        assert (resp.status_code, resp.json()["error"]["code"]) == (409, "upload_closed")
    assert await storage.get(path) == sealed
    await run_finalize(worker_dsn, admin, upload, storage, crypto, finish=True)
    assert upload_state(admin, upload)["status"] == "complete"
    for replacement in (blob(19, 100), SMALL):
        assert (await put(owner, upload, 1, replacement)).status_code == 409
    assert await storage.get(path) == sealed
    document = str(upload_state(admin, upload)["document_id"])
    record_clean_scan(document)  # the scan job is not run here; the download needs its verdict
    got = await fetch(owner, document_path(team.case, document))
    assert got.delivered_whole_file and got.body == SMALL


async def test_u3_complete_twice_is_one_document(
    client_for: ClientFor,
    team: Team,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    owner = await client_for(team.expert)
    upload = await upload_whole(owner, team.case, SMALL)
    first, second = await complete(owner, upload, sha(SMALL)), await complete(owner, upload, sha(SMALL))
    assert (first.status_code, second.status_code) == (202, 202)
    await run_finalize(worker_dsn, admin, upload, storage, crypto, finish=True)
    await run_finalize(worker_dsn, admin, upload, storage, crypto)  # a retried job adds nothing
    third = await complete(owner, upload, sha(SMALL))
    assert third.status_code == 200
    assert third.json()["document_id"] == str(upload_state(admin, upload)["document_id"])
    assert documents_at(admin, team.case, upload) == 1
    other_file = await complete(owner, upload, sha(b"another file"))  # a different file after it is done
    assert other_file.status_code == 409
    audits = admin.execute(
        "select count(*) from app.audit_log where target = %s and action = 'upload.completed'", (f"upload:{upload}",)
    ).fetchone()
    assert audits == (1,)  # a repeat while the job is live, or after it, writes nothing more


async def test_u3_cancelled_uploads_take_no_more_parts_and_cannot_complete(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    owner = await client_for(team.expert)
    live = await upload_whole(owner, team.case, SMALL)  # control upload, left alone
    upload = await upload_whole(owner, team.case, SMALL)
    path = object_path(team.case, upload, 1)
    sealed = await storage.get(path)
    assert (await owner.post(f"/v1/uploads/{upload}/cancel")).json()["status"] == "cancelled"
    assert (await put(owner, upload, 1, blob(20, 100))).status_code == 409
    assert (await complete(owner, upload, sha(SMALL))).status_code == 409
    assert (await owner.post(f"/v1/uploads/{upload}/cancel")).status_code == 409
    assert upload_state(admin, upload)["status"] == "cancelled" and documents_at(admin, team.case, upload) == 0
    assert await storage.get(path) == sealed
    assert (await complete(owner, live, sha(SMALL))).status_code == 202  # the neighbour still works


async def test_u3_an_expired_upload_takes_no_parts_and_cannot_complete(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    owner = await client_for(team.expert)
    fresh = await upload_whole(owner, team.case, SMALL)
    stale = await upload_whole(owner, team.case, SMALL)
    admin.execute("update app.uploads set expires_at = now() - interval '1 hour' where id = %s", (stale,))
    sealed = await storage.get(object_path(team.case, stale, 1))
    assert (await put(owner, stale, 1, blob(21, 100))).status_code == 409
    done = await complete(owner, stale, sha(SMALL))
    assert (done.status_code, done.json()["error"]["code"]) == (409, "upload_closed")
    assert await storage.get(object_path(team.case, stale, 1)) == sealed
    assert documents_at(admin, team.case, stale) == 0
    assert upload_state(admin, stale)["status"] != "finalizing"
    assert (await complete(owner, fresh, sha(SMALL))).status_code == 202  # control


async def test_u3_replaying_a_part_is_harmless_and_a_different_rewrite_is_caught_at_finalize(
    client_for: ClientFor,
    team: Team,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_dsn: str,
) -> None:
    good, other = blob(22, 100), blob(23, 100)
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, good)
    for _ in range(3):
        assert (await put(owner, upload, 1, good)).json() == {"received": [1]}
    assert await storage.list(upload_prefix(team.case, upload)) == [object_path(team.case, upload, 1)]
    # a rewrite with other bytes while open is accepted ...
    assert (await put(owner, upload, 1, other)).status_code == 200
    # ... but the file that results is not the one announced, so it never becomes a document
    assert (await complete(owner, upload, sha(good))).status_code == 202
    await run_finalize(worker_dsn, admin, upload, storage, crypto, finish=True)
    state = upload_state(admin, upload)
    assert (state["status"], state["failure"], state["document_id"]) == ("failed", "sha256_mismatch", None)
    assert documents_at(admin, team.case, upload) == 0
    # control: announcing the rewritten bytes at the start gives a document that serves those bytes
    mine = await open_upload(owner, team.case, other)
    assert (await put(owner, mine, 1, good)).status_code == 200
    assert (await put(owner, mine, 1, other)).status_code == 200
    document = await complete_and_finalize(owner, mine, sha(other), scanned=True)
    got = await fetch(owner, document_path(team.case, document))
    assert got.delivered_whole_file and got.body == other


# ---------------------------------------------------------------------------------------------------------
# U5 limits
# ---------------------------------------------------------------------------------------------------------


async def test_u5_size_limits_at_start(client_for: ClientFor, team: Team, admin: psycopg.Connection) -> None:
    owner = await client_for(team.expert)
    body = {"kind": "records", "filename": "f.bin", "sha256": "0" * 64}
    empty = await owner.post(f"/v1/cases/{team.case}/uploads", json={**body, "size": 0})
    assert (empty.status_code, empty.json()["error"]["code"]) == (422, "file_empty")
    negative = await owner.post(f"/v1/cases/{team.case}/uploads", json={**body, "size": -1})
    assert negative.status_code == 422
    over = await owner.post(f"/v1/cases/{team.case}/uploads", json={**body, "size": 500 * MB + 1})
    assert (over.status_code, over.json()["error"]["code"]) == (422, "file_too_large")
    assert rows_for(admin, team.case) == 0
    # control: exactly the largest file is accepted; claiming it and sending nothing stores nothing
    top = await owner.post(f"/v1/cases/{team.case}/uploads", json={**body, "size": 500 * MB})
    assert top.status_code == 201 and top.json()["chunk_count"] == 63 and top.json()["received"] == []
    # the open upload lives 7 days (migration 0007) and, once abandoned, its parts wait for the daily sweep:
    # a leftover part can therefore sit in Storage for up to about 8 days (U4).
    expires = datetime.fromisoformat(top.json()["expires_at"])
    assert timedelta(days=6, hours=23) < expires - datetime.now(UTC) <= timedelta(days=7, minutes=1)


async def test_u5_the_21st_open_upload_of_a_person_is_429(
    client_for: ClientFor, team: Team, admin: psycopg.Connection
) -> None:
    owner = await client_for(team.expert)
    first = [await open_upload(owner, team.case, SMALL) for _ in range(20)]
    refused = await begin(owner, team.case, SMALL)
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "too_many_uploads")
    assert rows_for(admin, team.case) == 20
    assert (await begin(owner, team.second_case, SMALL)).status_code == 429  # the count is per person, not per case
    # controls: another person is unaffected, and finishing one frees a place
    assert (await begin(await client_for(team.assistant), team.case, SMALL)).status_code == 201
    assert (await owner.post(f"/v1/uploads/{first[0]}/cancel")).status_code == 200
    assert (await begin(owner, team.case, SMALL)).status_code == 201


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 U5 low: no cap on the total bytes a case's open uploads may claim: one person can open "
    "20 uploads of 500 MB in one case (10 GB) and a second person 20 more; only the per-file 500 MB limit "
    "(C1) and 20 open uploads per person apply, and C2 (8,000 pages) is not checked at upload: "
    "api/app/core/routes/uploads.py:176-186 (start_upload)",
)
async def test_u5_the_open_uploads_of_one_case_cannot_claim_without_limit(client_for: ClientFor, team: Team) -> None:
    owner = await client_for(team.expert)
    body = {"kind": "records", "filename": "f.bin", "size": 500 * MB, "sha256": "0" * 64}
    codes = [(await owner.post(f"/v1/cases/{team.case}/uploads", json=body)).status_code for _ in range(4)]
    # Basis for the bound: C2 says the largest case is 8,000 pages and C1 puts 4,000 pages at 500 MB,
    # so more than about 1 GB of open claims in one case is already beyond a case's size.
    assert codes[0] == 201 and codes[-1] in (413, 422, 429), codes


# ---------------------------------------------------------------------------------------------------------
# V5 file names, V6 kinds
# ---------------------------------------------------------------------------------------------------------

ODD_NAMES = {
    "traversal": "../../etc/passwd",
    "backslashes": "..\\..\\windows\\system32\\drivers\\etc\\hosts",
    "quotes": 'a"b; filename="evil.exe',
    "multibyte_255": "界" * 255,
    "leading_dots": "...hidden",
    "percent": "%2e%2e%2fsecret",
}


@pytest.mark.parametrize("name", list(ODD_NAMES))
async def test_v5_odd_file_names_are_data_only_never_a_storage_key_or_a_header(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, storage: CaseFileStorage, name: str
) -> None:
    filename = ODD_NAMES[name]
    owner = await client_for(team.expert)
    resp = await begin(owner, team.case, SMALL, filename=filename)
    assert resp.status_code == 201, resp.text
    assert resp.json()["filename"] == filename  # kept as text, returned as a JSON string
    upload = resp.json()["upload_id"]
    assert (await put(owner, upload, 1, SMALL)).status_code == 200
    document = await complete_and_finalize(owner, upload, sha(SMALL), scanned=True)
    keys = await storage.list_recursive(f"cases/{team.case}")
    assert keys and all(key_pattern(team.case, upload).match(k) for k in keys), keys
    stored = admin.execute("select filename, storage_path from app.documents where id = %s", (document,)).fetchone()
    assert stored is not None and stored[0] == filename and stored[1] == upload_prefix(team.case, upload)
    got = await fetch(owner, document_path(team.case, document))
    assert got.delivered_whole_file and got.body == SMALL
    for headers in (resp.headers, got.headers):
        for header, value in headers.items():
            assert "\r" not in value and "\n" not in value
            assert filename[:6] not in value, header  # the download's fixed attachment name is not the file's


@pytest.mark.parametrize(
    "filename", ["a\r\nSet-Cookie: x=1", "a\nb", "a\rb", "a\x00b", "a\tb", "a\x1fb", "a\x7fb", "x" * 256, ""]
)
async def test_v5_control_characters_and_overlong_or_empty_names_are_refused(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, filename: str
) -> None:
    owner = await client_for(team.expert)
    resp = await begin(owner, team.case, SMALL, filename=filename)
    assert resp.status_code == 422, resp.text
    assert rows_for(admin, team.case) == 0
    assert (await begin(owner, team.case, SMALL, filename="plain.bin")).status_code == 201  # control


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V5b low: a file name with a right-to-left override (U+202E) or other bidi/line-separator "
    "characters is accepted and returned as is, so a name can display reversed (e.g. an extension spoof) wherever "
    "the screen shows it: api/app/core/routes/uploads.py:53 (CONTROL_CHARS covers only \\x00-\\x1f and \\x7f)",
)
async def test_v5_bidi_override_characters_in_a_file_name_are_refused(
    client_for: ClientFor, team: Team, admin: psycopg.Connection
) -> None:
    owner = await client_for(team.expert)
    refusals = []
    for name in ("report‮fdp.exe", "a b", "a⁦b", "a\x85b"):
        refusals.append((await begin(owner, team.case, SMALL, filename=name)).status_code)
    assert refusals == [422] * 4, refusals
    assert (await begin(owner, team.case, SMALL, filename="plain.bin")).status_code == 201  # control


@pytest.mark.parametrize("kind", ["Records", "9records", "a-b", "a b", "r" * 41, "", "records\n", "../x"])
async def test_v6_a_kind_that_breaks_the_code_pattern_is_refused(
    client_for: ClientFor, team: Team, admin: psycopg.Connection, kind: str
) -> None:
    owner = await client_for(team.expert)
    assert (await begin(owner, team.case, SMALL, kind=kind)).status_code == 422
    assert rows_for(admin, team.case) == 0
    assert (await begin(owner, team.case, SMALL, kind="records")).status_code == 201  # control: a core kind


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-12 V6 low: a `kind` that matches the code pattern but is not a document kind of the case's packs "
    "(e.g. 'zz_not_a_kind') is accepted at start and becomes documents.kind at finalize; the pack's "
    "document_kinds() (core/packs.py:552) is not consulted anywhere on the upload path: "
    "api/app/core/routes/uploads.py:61 (pattern only), api/app/core/routes/uploads.py:170-186, "
    "api/app/core/uploads.py:156 (_register inserts row['kind'] as sent)",
)
async def test_v6_a_kind_outside_the_case_packs_is_refused(
    client_for: ClientFor, team: Team, admin: psycopg.Connection
) -> None:
    owner = await client_for(team.expert)
    resp = await begin(owner, team.case, SMALL, kind="zz_not_a_kind")
    assert resp.status_code == 422, resp.status_code
    assert rows_for(admin, team.case) == 0
    assert (await begin(owner, team.case, SMALL, kind="records")).status_code == 201  # control


async def test_upload_helpers_send_whole_two_part_files(client_for: ClientFor, team: Team) -> None:
    """Guards the helper itself: a two-part file goes up and is the size the tests below assume."""
    data = blob(24, TWO_PART_SIZE)
    owner = await client_for(team.expert)
    upload = await open_upload(owner, team.case, data)
    await send_all(owner, upload, data)
    status = (await owner.get(f"/v1/uploads/{upload}")).json()
    assert status["chunk_count"] == 2 and status["received"] == [1, 2] and status["missing"] == []
    assert isinstance(owner, httpx.AsyncClient)
