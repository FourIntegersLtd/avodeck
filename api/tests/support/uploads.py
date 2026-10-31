"""Upload test helpers: sending a file, and running the `finalize_upload` job inline after a 202."""

from __future__ import annotations

import asyncio
import functools
import hashlib
import os
import uuid
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager

import httpx
import psycopg

from app.core.crypto import CaseCrypto
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import SupabaseStorage
from tests.support.files import SyntheticFile


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def start(client: httpx.AsyncClient, case: uuid.UUID, file: SyntheticFile, filename: str = "bundle.pdf") -> str:
    body = {"kind": "records", "filename": filename, "size": file.size, "sha256": file.sha256()}
    resp = await client.post(f"/v1/cases/{case}/uploads", json=body)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["upload_id"])


async def put(
    client: httpx.AsyncClient, upload: str, n: int, data: bytes, digest: str | None = "auto"
) -> httpx.Response:
    """Send part `n`. `digest`: its checksum header (default: the right one; None: leave the header off)."""
    headers = {"content-type": "application/octet-stream"}
    if digest is not None:
        headers["x-chunk-sha256"] = sha(data) if digest == "auto" else digest
    return await client.put(f"/v1/uploads/{upload}/chunks/{n}", content=data, headers=headers)


async def put_all(client: httpx.AsyncClient, upload: str, file: SyntheticFile) -> None:
    for n, data in file.chunks():
        assert (await put(client, upload, n, data)).status_code == 200


def complete(client: httpx.AsyncClient, upload: str, digest: str) -> Awaitable[httpx.Response]:
    return client.post(f"/v1/uploads/{upload}/complete", json={"sha256": digest})


def row(admin: psycopg.Connection, sql: str, *args: object) -> tuple[object, ...] | None:
    return admin.execute(sql, args).fetchone()


@asynccontextmanager
async def worker_database(worker_dsn: str, max_size: int = 2) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=max_size)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def run_finalize(
    worker_dsn: str,
    admin: psycopg.Connection,
    upload: str,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    *,
    attempt: int = 1,
) -> None:
    """Run the upload's `finalize_upload` job once, inline, as the worker would (raises as the job would)."""
    from app.core.uploads import finalize

    found = row(admin, "select finalize_job_id, case_id from app.uploads where id = %s", upload)
    assert found is not None and found[0] is not None, "the upload has no finalize job"
    job_id, case_id = found
    async with worker_database(worker_dsn) as database:
        ctx = JobContext(database, job_id, case_id, "finalize_upload", attempt)  # type: ignore[arg-type]
        await finalize(ctx, storage, crypto)


@functools.lru_cache(maxsize=1)
def _dsns() -> dict[str, str]:
    """The same connection strings as conftest's `test_database_dsns`, for helpers without fixtures."""
    from pathlib import Path

    from tests.support.local_supabase import local_db_port, test_dsns

    names = ("TEST_DATABASE_ADMIN_URL", "TEST_DATABASE_URL", "TEST_DATABASE_URL_DIRECT")
    repo = Path(__file__).resolve().parents[2]
    port = "" if all(os.environ.get(n) for n in names) else local_db_port(repo)
    return test_dsns(port, os.environ)


async def complete_and_finalize(client: httpx.AsyncClient, upload: str, digest: str) -> str:
    """Complete the upload, run its `finalize_upload` job inline, and return the document id.

    For helpers that hold only a client: the storage and case encryption come from the app it talks
    to, and the database from the test connection strings, as the fixtures do.
    """
    done = await complete(client, upload, digest)
    assert done.status_code == 202, done.text
    app = client._transport.app  # type: ignore[attr-defined]  # the test app behind httpx's ASGI transport
    dsns = _dsns()
    with psycopg.connect(dsns["TEST_DATABASE_ADMIN_URL"], autocommit=True, connect_timeout=5) as admin:
        await run_finalize(dsns["TEST_DATABASE_URL_DIRECT"], admin, upload, app.state.storage, app.state.crypto)
    status = await client.get(f"/v1/uploads/{upload}")
    assert status.status_code == 200, status.text
    document = status.json()["document_id"]
    assert document is not None, status.text
    return str(document)


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
