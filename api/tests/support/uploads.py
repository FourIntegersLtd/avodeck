"""Upload test helpers: sending a file, and running the `finalize_upload` job inline after a 202."""

from __future__ import annotations

import asyncio
import hashlib
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
