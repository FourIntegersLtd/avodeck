"""A stored file comes back exactly as it went up, or not at all (ADR 0010, architecture 5.3).

Each chunk is sealed to its own storage path, so a chunk moved into another chunk's place cannot be
opened. The download re-checks the whole file's SHA-256 as it streams and stops with an error when
a part is missing or changed, rather than sending a file that is not the one uploaded.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, CryptoError, LocalKeyProvider
from app.core.db import Database
from app.core.storage import SupabaseStorage
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
    document = (await client.post(f"/v1/uploads/{upload_id}/complete", json={"sha256": file.sha256()})).json()[
        "document_id"
    ]
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
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    path, prefix = await uploaded(client_for, team, admin)
    first, second = sorted(await storage.list(prefix))[:2]
    a, b = await storage.get(first), await storage.get(second)
    await storage.put(first, b)
    await storage.put(second, a)
    with pytest.raises(CryptoError):
        await download(client_for, team, path)


async def test_a_chunk_missing_from_storage_does_not_download(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, storage: SupabaseStorage
) -> None:
    path, prefix = await uploaded(client_for, team, admin)
    await storage.delete([sorted(await storage.list(prefix))[-1]])
    with pytest.raises(CryptoError):
        await download(client_for, team, path)
