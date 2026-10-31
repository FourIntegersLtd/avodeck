"""Slice B job: `export_transcript` builds the Word copy of the records transcript (ADR 0028).

The file holds the same entries as the screen, in the same order, with names and dates of birth stripped
(rule 7). It is stored sealed under the case key at `exports/<job>/transcript.docx`, downloaded once by the
person who asked, and removed by the daily close run with the other old exports. The download is audited.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from datetime import date
from typing import Any

import docx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import StorageError, SupabaseStorage
from app.features.reader import repo, transcript_jobs
from tests.support.db import scope_for
from tests.support.files import build_app, local_storage
from tests.support.identity import SENTENCE, leaked, record_identity
from tests.support.packs import LAUNCH
from tests.support.seed import Seed
from tests.support.transcript_world import Spec, add_document, add_page

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture(autouse=True)
def services(monkeypatch: pytest.MonkeyPatch, storage: SupabaseStorage, crypto: CaseCrypto) -> None:
    monkeypatch.setattr(transcript_jobs.services, "storage", storage)
    monkeypatch.setattr(transcript_jobs.services, "crypto", crypto)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: SupabaseStorage, crypto: CaseCrypto) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=crypto)


@pytest.fixture
async def aconn(admin_dsn: str) -> Any:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    return {"case": case, "expert": expert}


async def read(api_db: Database, w: dict[str, Any], *pages: Any) -> None:
    """The named expert has read these pages."""
    for built in pages:
        async with api_db.request(scope_for(w["expert"])) as conn:
            await repo.record_read(conn, w["case"], built.page_id, w["expert"], 30)


def paragraphs(data: bytes) -> list[str]:
    return [p.text for p in docx.Document(io.BytesIO(data)).paragraphs]


async def build(client: Any, worker_db: Database, admin: psycopg.Connection, w: dict[str, Any]) -> uuid.UUID:
    job = uuid.UUID((await client.post(f"/v1/cases/{w['case']}/transcript/download")).json()["job_id"])
    await transcript_jobs.export_transcript(JobContext(worker_db, job, w["case"], "export_transcript", 1))
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    return job


async def test_the_file_holds_the_entries_in_order_without_names_or_birth_dates(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    seed: Seed,
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    world = {"case": case, "expert": expert}
    await record_identity(aconn, crypto, world["case"])
    doc = await add_document(aconn, world["case"])
    two = await add_page(aconn, crypto, world["case"], doc, 2, [Spec("zz_session", ("second entry",), day=date(2020, 2, 1))])
    one = await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", (SENTENCE,), day=date(2020, 1, 1))])
    await read(api_db, world, one, two)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world)
    resp = await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "private, no-store"
    text = paragraphs(resp.content)
    assert leaked(text) == []
    joined = " ".join(text)
    assert joined.index("second entry") > joined.index("Seen with")  # the January entry comes first


async def test_the_copy_is_downloaded_once_and_audited(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    doc = await add_document(aconn, world["case"])
    page = await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))])
    await read(api_db, world, page)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world)
    assert (await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")).status_code == 200
    assert (await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")).status_code == 404
    row = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.transcript_downloaded'",
        (world["case"],),
    ).fetchone()
    assert row is not None and row[0] == 1


async def test_a_job_nobody_asked_for_builds_nothing(
    worker_db: Database, seed: Seed, storage: SupabaseStorage, world: dict[str, Any]
) -> None:
    job = seed.job(world["case"], kind="export_transcript")
    await transcript_jobs.export_transcript(JobContext(worker_db, job, world["case"], "export_transcript", 1))
    with pytest.raises(StorageError):
        await storage.get(transcript_jobs.transcript_path(world["case"], job))


async def test_the_stored_copy_is_sealed(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    doc = await add_document(aconn, world["case"])
    page = await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("plainword",))])
    await read(api_db, world, page)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world)
    blob = await storage.get(transcript_jobs.transcript_path(world["case"], job))
    assert b"plainword" not in blob and not blob.startswith(b"PK")


async def test_the_file_holds_only_pages_the_named_expert_has_read(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    doc = await add_document(aconn, world["case"])
    seen_page = await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("readword",))])
    await add_page(aconn, crypto, world["case"], doc, 2, [Spec("zz_session", ("unreadword",))])
    await read(api_db, world, seen_page)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world)
    resp = await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    joined = " ".join(paragraphs(resp.content))
    assert "readword" in joined and "unreadword" not in joined


async def test_nothing_is_built_while_the_named_expert_has_read_nothing(
    client_for: Any, aconn: psycopg.AsyncConnection, crypto: CaseCrypto, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("unreadword",))])
    client = await client_for(world["expert"])
    resp = await client.post(f"/v1/cases/{world['case']}/transcript/download")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "nothing_read"


async def test_an_organisation_admin_cannot_ask_for_the_file(
    client_for: Any, aconn: psycopg.AsyncConnection, crypto: CaseCrypto, seed: Seed
) -> None:
    expert, boss = seed.person(), seed.person()
    case = seed.case(seed.org(members={expert: "owner", boss: "admin"}), expert, status="active")
    doc = await add_document(aconn, case)
    await add_page(aconn, crypto, case, doc, 1, [Spec("zz_session", ("unreadword",))])
    client = await client_for(boss)
    assert (await client.post(f"/v1/cases/{case}/transcript/download")).status_code == 403


async def test_a_storage_fault_is_not_reported_as_already_downloaded(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    storage: SupabaseStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = await add_document(aconn, world["case"])
    await read(api_db, world, await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))]))
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world)
    real = storage.get

    async def broken(path: str) -> bytes:
        raise StorageError("storage answered 500")

    monkeypatch.setattr(storage, "get", broken)
    url = f"/v1/cases/{world['case']}/transcript/download/{job}"
    assert (await client.get(url)).status_code == 503
    monkeypatch.setattr(storage, "get", real)
    assert (await client.get(url)).status_code == 200  # the copy was not lost, nor counted as taken


async def test_the_copy_is_handed_over_once_even_if_removing_it_fails(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    storage: SupabaseStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = await add_document(aconn, world["case"])
    await read(api_db, world, await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))]))
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world)

    async def broken(paths: Any) -> None:
        raise StorageError("storage answered 500")

    monkeypatch.setattr(storage, "delete", broken)
    url = f"/v1/cases/{world['case']}/transcript/download/{job}"
    assert (await client.get(url)).status_code == 200
    assert (await client.get(url)).status_code == 404  # the audit row, not the file, says it was taken
