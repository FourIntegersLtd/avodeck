"""A stored file comes back exactly as it went up, or not at all (ADR 0010, architecture 5.3).

Each chunk is sealed to its own storage path, so a chunk moved into another chunk's place cannot be
opened. The download re-checks the whole file's SHA-256 as it streams: a part that is gone is a 404
before any bytes are sent, and a part that cannot be opened stops the read, rather than sending a
file that is not the one uploaded.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from fastapi import FastAPI

from app.core.case_files import StoredFileCorrupt
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.storage import CaseFileStorage
from tests.support.db import ClientFor
from tests.support.files import MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import complete_and_finalize

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=CaseCrypto(LocalKeyProvider.generate()))


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    return {"expert": expert, "case": seed.case(org, expert)}


async def uploaded(client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection) -> tuple[str, str]:
    """Uploads a made-up three-chunk file; returns its download path and storage prefix."""
    file = SyntheticFile(20 * MB, seed=23)
    client = await client_for(team["expert"])
    body = {"kind": "records", "filename": "bundle.pdf", "size": file.size, "sha256": file.sha256()}
    upload_id = (await client.post(f"/v1/cases/{team['case']}/uploads", json=body)).json()["upload_id"]
    for n, data in file.chunks():
        headers = {"content-type": "application/octet-stream"}
        assert (
            await client.put(
                f"/v1/uploads/{upload_id}/chunks/{n}",
                content=data,
                headers={**headers, "x-chunk-sha256": hashlib.sha256(data).hexdigest()},
            )
        ).status_code == 200
    document = await complete_and_finalize(client, upload_id, file.sha256())
    row = admin.execute("select storage_path from app.documents where id = %s", (document,)).fetchone()
    assert row is not None
    return f"/v1/cases/{team['case']}/documents/{document}/file", row[0]


async def download(client_for: ClientFor, team: dict[str, uuid.UUID], path: str) -> bytes:
    client = await client_for(team["expert"])
    out = b""
    async with client.stream("GET", path) as resp:
        async for part in resp.aiter_bytes():
            out += part
    return out


async def test_chunks_swapped_in_storage_do_not_download(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    path, prefix = await uploaded(client_for, team, admin)
    first, second = sorted(await storage.list(prefix))[:2]
    a, b = await storage.get(first), await storage.get(second)
    await storage.put(first, b)
    await storage.put(second, a)
    with pytest.raises(StoredFileCorrupt):
        await download(client_for, team, path)


async def test_a_chunk_missing_from_storage_does_not_download(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: CaseFileStorage
) -> None:
    path, prefix = await uploaded(client_for, team, admin)
    await storage.delete([sorted(await storage.list(prefix))[-1]])
    client = await client_for(team["expert"])
    assert (await client.get(path)).status_code == 404  # nothing is sent, not a stream that breaks half way
