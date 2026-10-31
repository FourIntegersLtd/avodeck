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
    status = admin.execute("select status from app.uploads where id = %s", (old,)).fetchone()
    assert status == ("expired",)


async def test_completed_uploads_are_never_expired(
    worker_db: Database, admin: psycopg.Connection, case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    case, expert, _ = case_and_expert
    done = add_upload(admin, case, expert, expires_in=timedelta(days=-1))
    admin.execute("update app.uploads set received = '{1,2}' where id = %s", (done,))  # parts arrive, then it closes
    admin.execute("update app.uploads set status = 'complete' where id = %s", (done,))
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


def set_job_status(admin: psycopg.Connection, job: uuid.UUID, status: str) -> None:
    """Put a job in any state (the queue's own guard only allows the real moves)."""
    admin.execute("set session_replication_role = replica")
    admin.execute("update app.jobs set status = %s where id = %s", (status, job))
    admin.execute("set session_replication_role = default")


def finalizing_upload(
    admin: psycopg.Connection, case: uuid.UUID, person: uuid.UUID, job_status: str, *, expires_in: timedelta
) -> uuid.UUID:
    job = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'finalize_upload') returning id", (case,)
    ).fetchone()
    assert job is not None
    set_job_status(admin, job[0], job_status)
    upload = add_upload(admin, case, person, expires_in=expires_in)
    admin.execute(
        "update app.uploads set status = 'finalizing', finalize_job_id = %s, finalizing_at = now() where id = %s",
        (job[0], upload),
    )
    return upload


@pytest.mark.parametrize("job_status", ["done", "failed", "queued", "paused"])
async def test_a_finalizing_upload_past_expiry_expires_unless_its_job_is_running(
    worker_db: Database,
    admin: psycopg.Connection,
    case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    job_status: str,
) -> None:
    """A job held back (case on hold) or never picked up must not leave the upload `finalizing` for ever."""
    case, expert, _ = case_and_expert
    upload = finalizing_upload(admin, case, expert, job_status, expires_in=timedelta(days=-1))
    async with worker_db.anonymous() as conn:
        assert upload in await expire_uploads(conn, date.today(), None)
    assert admin.execute("select status from app.uploads where id = %s", (upload,)).fetchone() == ("expired",)


async def test_a_finalizing_upload_with_a_running_job_is_never_expired(
    worker_db: Database, admin: psycopg.Connection, case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    case, expert, _ = case_and_expert
    upload = finalizing_upload(admin, case, expert, "running", expires_in=timedelta(days=-1))
    async with worker_db.anonymous() as conn:
        assert upload not in await expire_uploads(conn, date.today(), None)
    assert admin.execute("select status from app.uploads where id = %s", (upload,)).fetchone() == ("finalizing",)


@pytest.mark.parametrize("status", ["cancelled", "failed"])
async def test_cancelled_and_failed_uploads_are_returned_for_cleanup_until_cleaned(
    worker_db: Database,
    storage: SupabaseStorage,
    admin: psycopg.Connection,
    case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    status: str,
) -> None:
    case, expert, _ = case_and_expert
    upload = add_upload(admin, case, expert, expires_in=timedelta(days=3))
    failure = ", failure = 'chunk_unreadable'" if status == "failed" else ""
    admin.execute(f"update app.uploads set status = %s{failure} where id = %s", (status, upload))
    await storage.put(f"{upload_prefix(case, upload)}/00001", b"chunk")
    async with worker_db.anonymous() as conn:
        assert upload in await expire_uploads(conn, date.today(), storage)
    assert await storage.list(upload_prefix(case, upload)) == []
    assert admin.execute("select cleaned_at is not null from app.uploads where id = %s", (upload,)).fetchone() == (
        True,
    )
    async with worker_db.anonymous() as conn:  # cleaned: not returned again
        assert upload not in await expire_uploads(conn, date.today(), storage)


async def test_cleaned_at_is_not_set_while_storage_still_holds_the_parts(
    worker_db: Database,
    storage: SupabaseStorage,
    admin: psycopg.Connection,
    case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case, expert, _ = case_and_expert
    upload = add_upload(admin, case, expert, expires_in=timedelta(hours=-1))
    await storage.put(f"{upload_prefix(case, upload)}/00001", b"chunk")

    async def refuses(paths: list[str]) -> None:
        return None  # a delete that did nothing

    monkeypatch.setattr(storage, "delete", refuses)
    async with worker_db.anonymous() as conn:
        await expire_uploads(conn, date.today(), storage)
    assert admin.execute("select cleaned_at is null from app.uploads where id = %s", (upload,)).fetchone() == (True,)


async def test_an_upload_with_a_document_is_never_returned_or_marked_cleaned(
    worker_db: Database, admin: psycopg.Connection, case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID]
) -> None:
    case, expert, _ = case_and_expert
    upload = finalizing_upload(admin, case, expert, "done", expires_in=timedelta(days=3))
    doc = admin.execute(
        "insert into app.documents (case_id, kind, filename, sha256) values (%s, 'records', 'f.pdf', %s) returning id",
        (case, "0" * 64),
    ).fetchone()
    assert doc is not None
    admin.execute("update app.uploads set status = 'complete', document_id = %s where id = %s", (doc[0], upload))
    async with worker_db.anonymous() as conn:
        assert upload not in await expire_uploads(conn, date.today(), None)
        await conn.execute("select app.mark_upload_cleaned(%s)", (upload,))
    assert admin.execute("select cleaned_at is null from app.uploads where id = %s", (upload,)).fetchone() == (True,)


async def test_a_database_error_on_one_row_does_not_abort_the_sweeps_transaction(
    worker_db: Database,
    storage: SupabaseStorage,
    admin: psycopg.Connection,
    case_and_expert: tuple[uuid.UUID, uuid.UUID, uuid.UUID],
) -> None:
    case, expert, _ = case_and_expert
    first = add_upload(admin, case, expert, expires_in=timedelta(hours=-2))
    second = add_upload(admin, case, expert, expires_in=timedelta(hours=-2))

    class Flaky:
        """Passes everything through, but the marking of `first` fails inside the database."""

        def __init__(self, conn: psycopg.AsyncConnection) -> None:
            self.conn = conn

        async def execute(self, query: str, params: object = None) -> object:
            if "mark_upload_cleaned" in query and params == (first,):
                return await self.conn.execute("select 1 / 0")
            return await self.conn.execute(query, params)  # type: ignore[call-overload]

        def transaction(self) -> object:
            return self.conn.transaction()

    async with worker_db.anonymous() as conn:
        await expire_uploads(Flaky(conn), date.today(), storage)  # type: ignore[arg-type]
        await conn.execute("select 1")  # the sweep's own transaction is still usable
    got: dict[uuid.UUID, bool] = dict(
        admin.execute(
            "select id, cleaned_at is not null from app.uploads where id = any(%s)", ([first, second],)
        ).fetchall()
    )
    assert got == {first: False, second: True}
