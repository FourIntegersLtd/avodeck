"""Structured upload events beyond the audit rows (plan 3g): ids, counts and durations only (rule 6).

`upload.started` when an upload opens, `upload.chunk_resent` when a part already stored is sent again,
`upload.expired` and `upload.cleaned` from the daily sweep. None carries a file name, a path or any text.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.housekeeping import expire_uploads
from app.core.storage import SupabaseStorage, upload_prefix
from tests.support.db import ClientFor
from tests.support.files import MB, SyntheticFile, build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import put, start

pytestmark = pytest.mark.anyio
FILE = SyntheticFile(10 * MB, seed=31)


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


def events(caplog: pytest.LogCaptureFixture, name: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage() == name]


async def test_starting_an_upload_logs_its_ids_and_counts_but_never_the_file_name(
    client_for: ClientFor, team: dict[str, uuid.UUID], caplog: pytest.LogCaptureFixture
) -> None:
    client = await client_for(team["expert"])
    with caplog.at_level(logging.INFO):
        upload = await start(client, team["case"], FILE, filename="secret-name.pdf")
    (event,) = events(caplog, "upload.started")
    assert event.upload_id == upload and event.case_id == str(team["case"])  # type: ignore[attr-defined]
    assert event.chunk_count == 2 and event.size == FILE.size  # type: ignore[attr-defined]
    assert "secret-name" not in caplog.text


async def test_a_part_sent_again_logs_chunk_resent_with_its_number_only(
    client_for: ClientFor, team: dict[str, uuid.UUID], caplog: pytest.LogCaptureFixture
) -> None:
    client = await client_for(team["expert"])
    upload = await start(client, team["case"], FILE)
    n, data = next(iter(FILE.chunks()))
    await put(client, upload, n, data)
    assert events(caplog, "upload.chunk_resent") == []  # the first send is not a resend
    with caplog.at_level(logging.INFO):
        await put(client, upload, n, data)
    (event,) = events(caplog, "upload.chunk_resent")
    assert event.upload_id == upload and event.part == n  # type: ignore[attr-defined]


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


async def test_the_sweep_logs_upload_expired_and_upload_cleaned_with_counts_and_ids_only(
    worker_db: Database,
    storage: SupabaseStorage,
    admin: psycopg.Connection,
    team: dict[str, uuid.UUID],
    caplog: pytest.LogCaptureFixture,
) -> None:
    uid = uuid.uuid4()
    admin.execute(
        "insert into app.uploads (id, case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " expires_at) values (%s, %s, %s, 'records', 'secret-name.pdf', 10, %s, 8, 2, now() - interval '1 hour')",
        (uid, team["case"], team["expert"], "0" * 64),
    )
    await storage.put(f"{upload_prefix(team['case'], uid)}/00001", b"chunk")
    with caplog.at_level(logging.INFO):
        async with worker_db.anonymous() as conn:
            await expire_uploads(conn, date.today() + timedelta(days=1), storage)
    # Other terminal uploads in the same database may be swept too: look at this one's events only.
    (expired,) = [e for e in events(caplog, "upload.expired") if e.upload_id == str(uid)]  # type: ignore[attr-defined]
    (cleaned,) = [e for e in events(caplog, "upload.cleaned") if e.upload_id == str(uid)]  # type: ignore[attr-defined]
    assert cleaned.parts == 1  # type: ignore[attr-defined]
    assert "secret-name" not in caplog.text
