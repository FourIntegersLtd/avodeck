"""No database connection is held across slow I/O; heavy CPU work runs off the event loop (scalability PR 9).

- `PUT /v1/uploads/{id}/chunks/{n}`: the connection is back in the pool while the body is read, hashed,
  encrypted and put to Storage; two calls for the same part still leave one stored part.
- `GET /v1/cases/{id}/pages/{n}/image`: no connection during the Storage get; a stranger still gets 404
  and the named expert still gets the picture.
- Sealing and opening anything over 64 KiB runs on a worker thread, not on the loop.
"""

from __future__ import annotations

import asyncio
import threading
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core import crypto as crypto_module
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.storage import CaseFileStorage, upload_prefix
from app.features.pipeline.paths import page_image_path
from tests.support.files import MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import put, row, start

pytestmark = pytest.mark.anyio

SLOW = 0.3


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
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, stranger = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    return {"case": case, "expert": expert, "stranger": stranger}


def in_use(database: Database) -> int:
    stats = database.pool_stats()
    return stats["pool_size"] - stats["pool_available"]


def slow(database: Database, seen: list[int], original: Callable[..., Any]) -> Callable[..., Any]:
    """A Storage call that takes a while and notes how many pooled connections are in use meanwhile."""

    async def call(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(SLOW)
        seen.append(in_use(database))
        return await original(*args, **kwargs)

    return call


async def test_a_part_is_put_to_storage_with_no_connection_held(
    client_for: Any,
    world: dict[str, uuid.UUID],
    api_db: Database,
    storage: CaseFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []
    monkeypatch.setattr(storage, "put", slow(api_db, seen, storage.put))
    file = SyntheticFile(1 * MB, seed=91)
    client = await client_for(world["expert"])
    upload = await start(client, world["case"], file)
    seen.clear()
    got = await put(client, upload, 1, file.chunk(1))
    assert got.status_code == 200, got.text
    assert got.json() == {"received": [1]}
    assert seen == [0]


async def test_two_calls_for_the_same_part_leave_one_stored_part(
    client_for: Any,
    world: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    api_db: Database,
    storage: CaseFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[int] = []
    monkeypatch.setattr(storage, "put", slow(api_db, seen, storage.put))
    file = SyntheticFile(1 * MB, seed=92)
    one, two = await client_for(world["expert"]), await client_for(world["expert"])
    upload = await start(one, world["case"], file)
    first, second = await asyncio.gather(put(one, upload, 1, file.chunk(1)), put(two, upload, 1, file.chunk(1)))
    assert (first.status_code, second.status_code) == (200, 200), (first.text, second.text)
    assert row(admin, "select received from app.uploads where id = %s", upload) == ([1],)
    assert len(await storage.list(upload_prefix(world["case"], uuid.UUID(upload)))) == 1


async def test_a_part_sent_again_with_other_bytes_replaces_the_stored_one(
    client_for: Any, world: dict[str, uuid.UUID], storage: CaseFileStorage, crypto: CaseCrypto, aconn: Any
) -> None:
    file = SyntheticFile(1 * MB, seed=93)
    client = await client_for(world["expert"])
    upload = await start(client, world["case"], file)
    assert (await put(client, upload, 1, file.chunk(1))).status_code == 200
    other = b"y" * len(file.chunk(1))
    assert (await put(client, upload, 1, other)).status_code == 200
    path = f"{upload_prefix(world['case'], uuid.UUID(upload))}/00001"
    assert await crypto.decrypt(aconn, world["case"], await storage.get(path), purpose=path) == other


async def test_the_page_picture_is_fetched_with_no_connection_held(
    seed: Seed,
    client_for: Any,
    aconn: Any,
    admin: psycopg.Connection,
    api_db: Database,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    page = seed.page(world["case"], seed.document(world["case"]), 1)
    path = page_image_path(world["case"], page)
    admin.execute("update app.pages set image_path = %s where id = %s", (path, page))
    picture = b"\xff\xd8\xff synthetic picture"
    await storage.put(path, await crypto.encrypt(aconn, world["case"], picture, purpose=path))
    seen: list[int] = []
    monkeypatch.setattr(storage, "get", slow(api_db, seen, storage.get))
    url = f"/v1/cases/{world['case']}/pages/1/image"

    stranger = await (await client_for(world["stranger"])).get(url)
    assert stranger.status_code == 404
    assert seen == []  # refused before anything was fetched

    got = await (await client_for(world["expert"])).get(url)
    assert got.status_code == 200
    assert got.content == picture
    assert got.headers["cache-control"] == "private, no-store"
    assert seen == [0]


@pytest.mark.parametrize(("size", "off_the_loop"), [(60 * 1024, False), (64 * 1024 + 1, True), (2 * MB, True)])
async def test_sealing_and_opening_a_large_blob_runs_off_the_event_loop(
    aconn: Any,
    world: dict[str, uuid.UUID],
    crypto: CaseCrypto,
    monkeypatch: pytest.MonkeyPatch,
    size: int,
    off_the_loop: bool,
) -> None:
    ran_on_loop: list[bool] = []
    for name in ("_seal", "_open"):
        original = getattr(crypto_module, name)

        def spy(*args: Any, _original: Callable[..., Any] = original) -> Any:
            ran_on_loop.append(threading.current_thread() is threading.main_thread())
            return _original(*args)

        monkeypatch.setattr(crypto_module, name, spy)
    data = b"z" * size
    blob = await crypto.encrypt(aconn, world["case"], data, purpose="p")
    assert await crypto.decrypt(aconn, world["case"], blob, purpose="p") == data
    assert ran_on_loop == [not off_the_loop] * 2
