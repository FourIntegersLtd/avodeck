"""Contract tests for the upload hardening (fix/upload-hardening step 0).

These tests define the schema and behaviour contract that streams A, B and C build against.
They are expected to fail until the corresponding implementation is done.
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
from app.core.storage import SupabaseStorage
from tests.support.db import ClientFor
from tests.support.files import MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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


async def start(client: httpx.AsyncClient, case: uuid.UUID, file: SyntheticFile) -> dict:
    body = {"kind": "records", "filename": "bundle.pdf", "size": file.size, "sha256": file.sha256()}
    resp = await client.post(f"/v1/cases/{case}/uploads", json=body)
    assert resp.status_code == 201, resp.text
    return resp.json()


# --- Upload response schema (new fields) ---


async def test_upload_response_includes_new_fields(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    """The Upload schema exposes failure, expires_at, sha256, filename and kind."""
    file = SyntheticFile(1 * MB, seed=101)
    client = await client_for(team["expert"])
    data = await start(client, team["case"], file)
    assert "failure" in data
    assert "expires_at" in data
    assert "sha256" in data
    assert "filename" in data
    assert "kind" in data
    assert data["failure"] is None
    assert data["sha256"] == file.sha256()
    assert data["filename"] == "bundle.pdf"
    assert data["kind"] == "records"
    assert data["expires_at"] is not None  # ISO timestamp


async def test_get_upload_includes_new_fields(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    file = SyntheticFile(1 * MB, seed=102)
    client = await client_for(team["expert"])
    data = await start(client, team["case"], file)
    resp = await client.get(f"/v1/uploads/{data['upload_id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["sha256"] == file.sha256()
    assert body["filename"] == "bundle.pdf"
    assert body["kind"] == "records"
    assert body["expires_at"] is not None


# --- Cancel ---


async def test_cancel_open_upload(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    """POST /v1/uploads/{id}/cancel on an open upload returns 200 with status cancelled."""
    file = SyntheticFile(1 * MB, seed=103)
    client = await client_for(team["expert"])
    data = await start(client, team["case"], file)
    upload_id = data["upload_id"]
    resp = await client.post(f"/v1/uploads/{upload_id}/cancel")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "cancelled"
    row = admin.execute("select status from app.uploads where id = %s", (upload_id,)).fetchone()
    assert row is not None and row[0] == "cancelled"


async def test_cancel_completed_upload_returns_409(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    file = SyntheticFile(1 * MB, seed=104)
    client = await client_for(team["expert"])
    data = await start(client, team["case"], file)
    upload_id = data["upload_id"]
    # Upload all chunks and complete
    for n, chunk in file.chunks():
        headers = {"content-type": "application/octet-stream", "x-chunk-sha256": sha(chunk)}
        resp = await client.put(f"/v1/uploads/{upload_id}/chunks/{n}", content=chunk, headers=headers)
        assert resp.status_code == 200, resp.text
    resp = await client.post(f"/v1/uploads/{upload_id}/complete", json={"sha256": file.sha256()})
    assert resp.status_code == 202, resp.text
    # Cancel should fail
    resp = await client.post(f"/v1/uploads/{upload_id}/cancel")
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "upload_closed"


async def test_cancel_prevents_further_chunks(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    file = SyntheticFile(17 * MB, seed=105)
    client = await client_for(team["expert"])
    data = await start(client, team["case"], file)
    upload_id = data["upload_id"]
    await client.post(f"/v1/uploads/{upload_id}/cancel")
    chunk = file.chunk(1)
    headers = {"content-type": "application/octet-stream", "x-chunk-sha256": sha(chunk)}
    resp = await client.put(f"/v1/uploads/{upload_id}/chunks/1", content=chunk, headers=headers)
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "upload_closed"


# --- DB state transitions (repository level) ---


async def test_db_allows_open_to_finalizing(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    """The guard allows open → finalizing when job id and time are set."""
    # Create a job row first
    job_id = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'finalize_upload') returning id",
        (team["case"],),
    ).fetchone()[0]
    # Create an upload
    upload_id = admin.execute(
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count)"
        " values (%s, %s, 'records', 'test.pdf', 1000, %s, %s, 1) returning id",
        (team["case"], team["expert"], "a" * 64, 8 * MB),
    ).fetchone()[0]
    admin.commit()
    admin.execute(
        "update app.uploads set status = 'finalizing', finalize_job_id = %s, finalizing_at = now() where id = %s",
        (job_id, upload_id),
    )
    admin.commit()
    row = admin.execute("select status from app.uploads where id = %s", (upload_id,)).fetchone()
    assert row[0] == "finalizing"


async def test_db_refuses_open_to_finalizing_without_job(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    """The guard refuses open → finalizing if finalize_job_id is null."""
    upload_id = admin.execute(
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count)"
        " values (%s, %s, 'records', 'test.pdf', 1000, %s, %s, 1) returning id",
        (team["case"], team["expert"], "b" * 64, 8 * MB),
    ).fetchone()[0]
    admin.commit()
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.uploads set status = 'finalizing' where id = %s", (upload_id,))
    assert err.value.sqlstate == "AV409"
    assert "finalizing needs a job reference and time" in str(err.value)


async def test_db_allows_finalizing_to_open_reopen(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    """The guard allows finalizing → open (repair: a missing part is removed from received)."""
    job_id = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'finalize_upload') returning id",
        (team["case"],),
    ).fetchone()[0]
    upload_id = admin.execute(
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count, received)"
        " values (%s, %s, 'records', 'test.pdf', 16000000, %s, %s, 2, '{1,2}') returning id",
        (team["case"], team["expert"], "c" * 64, 8 * MB),
    ).fetchone()[0]
    admin.commit()
    admin.execute(
        "update app.uploads set status = 'finalizing', finalize_job_id = %s, finalizing_at = now() where id = %s",
        (job_id, upload_id),
    )
    admin.commit()
    # Reopen: remove chunk 2 from received and go back to open
    admin.execute(
        "update app.uploads set status = 'open', received = '{1}', chunk_sha256 = '{}' where id = %s",
        (upload_id,),
    )
    admin.commit()
    row = admin.execute("select status, received from app.uploads where id = %s", (upload_id,)).fetchone()
    assert row[0] == "open"
    assert row[1] == [1]


# --- Filename and empty file validation ---


async def test_empty_file_is_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    """size=0 is already covered by gt=0 in the schema; verify it returns 422."""
    client = await client_for(team["expert"])
    resp = await client.post(
        f"/v1/cases/{team['case']}/uploads",
        json={"kind": "records", "filename": "empty.pdf", "size": 0, "sha256": "a" * 64},
    )
    assert resp.status_code == 422


# --- Expire function returns status column ---


async def test_expire_returns_status_column(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    """expire_uploads now returns (case_id, upload_id, status) not just two columns."""
    upload_id = admin.execute(
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " expires_at) values (%s, %s, 'records', 'test.pdf', 1000, %s, %s, 1, now() - interval '1 hour')"
        " returning id",
        (team["case"], team["expert"], "d" * 64, 8 * MB),
    ).fetchone()[0]
    admin.commit()
    rows = admin.execute("select * from app.expire_uploads(current_date)").fetchall()
    found = [r for r in rows if r[1] == upload_id]
    assert len(found) == 1
    assert len(found[0]) == 3  # (case_id, upload_id, status)
    assert found[0][2] == "expired"


# --- mark_upload_cleaned ---


async def test_mark_upload_cleaned_sets_timestamp(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    upload_id = admin.execute(
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " expires_at) values (%s, %s, 'records', 'test.pdf', 1000, %s, %s, 1,"
        " now() - interval '1 hour') returning id",
        (team["case"], team["expert"], "e" * 64, 8 * MB),
    ).fetchone()[0]
    # Expire it via the function (guard forbids direct insert with status='expired')
    admin.execute("select app.expire_uploads(current_date)")
    assert admin.execute("select status from app.uploads where id = %s", (upload_id,)).fetchone()[0] == "expired"
    admin.execute("select app.mark_upload_cleaned(%s)", (upload_id,))
    row = admin.execute("select cleaned_at from app.uploads where id = %s", (upload_id,)).fetchone()
    assert row[0] is not None


# --- case_files.py stub exists and imports ---


def test_case_files_stub_imports() -> None:
    """The StoredDocument stub is importable."""
    from app.core.case_files import StoredDocument, StoredFileCorrupt, StoredFileMissing  # noqa: F401


async def test_spool_raises_not_implemented() -> None:
    """spool() raises NotImplementedError (stub)."""
    from app.core.case_files import StoredDocument

    doc = StoredDocument(
        storage=None,  # type: ignore[arg-type]
        crypto=None,  # type: ignore[arg-type]
        case_id=uuid.uuid4(),
        prefix="test/prefix",
        paths=[],
    )
    with pytest.raises(NotImplementedError):
        await doc.spool(None)  # type: ignore[arg-type]
