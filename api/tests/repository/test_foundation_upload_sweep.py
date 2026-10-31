"""The daily upload sweep (state-machines.md "Upload"; foundation brief step 7 "Not yet").

An upload left unfinished past `expires_at` becomes `expired`, its chunks are removed from private
Storage, and an `upload.expired` audit row records it (person, organisation, case, action, target).
The sweep is a schedule keyed by date (F28): each date runs once, as `app_worker`, never the service role.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date, timedelta

import psycopg
import pytest

from app.core.db import Database
from app.core.housekeeping import UPLOAD_SWEEP, expire_uploads
from app.core.storage import SupabaseStorage, upload_prefix
from tests.support.files import local_storage
from tests.support.jobs import isolated_registry, open_worker
from tests.support.seed import Seed

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


def add_upload(admin: psycopg.Connection, case: uuid.UUID, person: uuid.UUID, *, expires_in: timedelta) -> uuid.UUID:
    uid = uuid.uuid4()
    admin.execute(
        "insert into app.uploads (id, case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " expires_at) values (%s, %s, %s, 'records', 'f.pdf', 10, %s, 8, 2, now() + %s)",
        (uid, case, person, "0" * 64, expires_in),
    )
    return uid


@pytest.fixture
def case_and_expert(seed: Seed) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return seed.case(org, expert), expert, org


async def test_unfinished_uploads_past_expiry_are_expired_their_chunks_removed_and_audited(
    worker_db: Database,
    storage: SupabaseStorage,
    admin: psycopg.Connection,
    case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    case, expert, org = case_and_expert
    old = add_upload(admin, case, expert, expires_in=timedelta(hours=-1))
    fresh = add_upload(admin, case, expert, expires_in=timedelta(days=3))
    for upload in (old, fresh):
        for n in (1, 2):
            await storage.put(f"{upload_prefix(case, upload)}/{n:05d}", b"chunk")
    async with worker_db.anonymous() as conn:
        expired = await expire_uploads(conn, date.today(), storage)
    assert old in expired and fresh not in expired
    statuses: dict[uuid.UUID, str] = dict(
        admin.execute("select id, status from app.uploads where id = any(%s)", ([old, fresh],)).fetchall()
    )
    assert statuses == {old: "expired", fresh: "open"}
    assert await storage.list(upload_prefix(case, old)) == []
    assert len(await storage.list(upload_prefix(case, fresh))) == 2
    rows = admin.execute(
        "select person_id, organisation_id, case_id, action, target from app.audit_log where target = %s",
        (f"upload:{old}",),
    ).fetchall()
    assert rows == [(None, org, case, "upload.expired", f"upload:{old}")]


async def test_the_sweep_only_expires_what_was_due_by_its_run_date(
    worker_db: Database, admin: psycopg.Connection, case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    case, expert, _ = case_and_expert
    old = add_upload(admin, case, expert, expires_in=timedelta(days=-3))
    async with worker_db.anonymous() as conn:
        assert old not in await expire_uploads(conn, date.today() - timedelta(days=5), None)
        assert old in await expire_uploads(conn, date.today(), None)
    # After expiring, the upload appears again as a terminal row needing cleanup (cleaned_at is null);
    # but its status must not change again.
    assert admin.execute("select status from app.uploads where id = %s", (old,)).fetchone()[0] == "expired"


async def test_completed_uploads_are_never_expired(
    worker_db: Database, admin: psycopg.Connection, case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    case, expert, _ = case_and_expert
    done = add_upload(admin, case, expert, expires_in=timedelta(days=-1))
    admin.execute("update app.uploads set received = '{1,2}', status = 'complete' where id = %s", (done,))
    async with worker_db.anonymous() as conn:
        assert done not in await expire_uploads(conn, date.today(), None)
    assert admin.execute("select status from app.uploads where id = %s", (done,)).fetchone() == ("complete",)


async def test_only_the_worker_may_sweep(api_dsn: str) -> None:
    with psycopg.connect(api_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from app.expire_uploads(current_date)")


async def test_the_sweep_is_a_daily_schedule_run_once_per_date(
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    import app.worker.main  # noqa: F401 - registers the app's schedules
    from app.core.jobs import registry as app_registry

    assert UPLOAD_SWEEP in app_registry.schedules
    case, expert, _ = case_and_expert
    old = add_upload(admin, case, expert, expires_in=timedelta(days=-1))
    registry = isolated_registry()
    name = f"{UPLOAD_SWEEP}_{uuid.uuid4().hex[:8]}"
    registry.schedule(name)(app_registry.schedules[UPLOAD_SWEEP])
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert await worker.catch_up(name, date.today()) == [date.today()]
        assert await worker.catch_up(name, date.today()) == []
    assert admin.execute("select status from app.uploads where id = %s", (old,)).fetchone() == ("expired",)
