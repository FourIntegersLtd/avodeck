"""Orphaned upload objects are found by folder and removed only when that is certain (R8).

`reconcile_upload_objects` looks at every `cases/{case}/uploads/{folder}/` of a case. A folder goes only
when its name is a UUID and either its row is terminal without a document (then the row is marked
cleaned, after the delete worked) or there is no row at all and every object is older than 8 days.
Everything else is counted and left. Counts only are logged: never a path, a filename or any text.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.housekeeping import ReconcileResult, reconcile_upload_objects
from app.core.storage import CaseFileStorage, StorageError, case_prefix, upload_prefix
from tests.support.files import age_objects, local_storage
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
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    store.list_page_size = 2  # more folders and objects than one page, so paging is exercised
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)


def add_upload(
    admin: psycopg.Connection, case: uuid.UUID, status: str = "open", *, document: bool = False
) -> uuid.UUID:
    person = admin.execute("select named_expert_id from app.cases where id = %s", (case,)).fetchone()
    assert person is not None
    uid = uuid.uuid4()
    admin.execute(
        "insert into app.uploads (id, case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " expires_at) values (%s, %s, %s, 'records', 'f.pdf', 10, %s, 8, 2, now() + interval '1 day')",
        (uid, case, person[0], "0" * 64),
    )
    if status != "open":
        doc = admin.execute(
            "select id from app.documents where bundle_id = (select bundle_id from app.cases where id = %s) limit 1",
            (case,),
        ).fetchone()
        admin.execute(
            "update app.uploads set status = %s, document_id = %s where id = %s",
            (status, doc[0] if document and doc else None, uid),
        )
    return uid


async def put_chunks(storage: CaseFileStorage, case: uuid.UUID, folder: str, count: int = 3) -> list[str]:
    paths = [f"{case_prefix(case)}/uploads/{folder}/{n:05d}" for n in range(1, count + 1)]
    for path in paths:
        await storage.put(path, b"chunk")
    return paths


async def reconcile(worker_db: Database, storage: CaseFileStorage, case: uuid.UUID) -> object:
    async with worker_db.job(case) as conn:
        return await reconcile_upload_objects(conn, storage, case)


async def test_deletes_verified_orphans_only(
    worker_db: Database, storage: CaseFileStorage, admin: psycopg.Connection, case: uuid.UUID, seed: Seed
) -> None:
    seed.document(case)
    cancelled = add_upload(admin, case, "cancelled")
    failed = add_upload(admin, case, "failed")
    complete = add_upload(admin, case, "complete", document=True)
    unknown_old, unknown_young = uuid.uuid4(), uuid.uuid4()
    for u in (cancelled, failed):
        await put_chunks(storage, case, str(u))
    kept = {
        complete: await put_chunks(storage, case, str(complete)),
        unknown_young: await put_chunks(storage, case, str(unknown_young)),
    }
    old_paths = await put_chunks(storage, case, str(unknown_old))
    age_objects(old_paths, days=9)
    stray = await put_chunks(storage, case, "not-a-uuid", 1)
    age_objects(stray, days=30)

    await reconcile(worker_db, storage, case)

    for gone in (cancelled, failed, unknown_old):
        assert await storage.list(upload_prefix(case, gone)) == []
    remaining = set(await storage.list_recursive(case_prefix(case)))
    assert set(stray) <= remaining  # a name that is not a UUID is never touched
    for paths in kept.values():
        assert set(paths) <= remaining
    cleaned: dict[uuid.UUID, bool] = dict(
        admin.execute("select id, cleaned_at is not null from app.uploads where case_id = %s", (case,)).fetchall()
    )
    assert cleaned == {cancelled: True, failed: True, complete: False}


async def test_a_live_open_upload_is_left_alone(
    worker_db: Database, storage: CaseFileStorage, admin: psycopg.Connection, case: uuid.UUID
) -> None:
    live = add_upload(admin, case)
    paths = await put_chunks(storage, case, str(live))
    age_objects(paths, days=30)  # old objects, but the row is live: the row decides
    await reconcile(worker_db, storage, case)
    assert len(await storage.list(upload_prefix(case, live))) == 3
    row = admin.execute("select status, cleaned_at from app.uploads where id = %s", (live,)).fetchone()
    assert row == ("open", None)


async def test_a_young_folder_without_a_row_is_left_alone(
    worker_db: Database, storage: CaseFileStorage, admin: psycopg.Connection, case: uuid.UUID
) -> None:
    folder = uuid.uuid4()  # an upload being created right now may not have its row visible yet
    paths = await put_chunks(storage, case, str(folder))
    age_objects(paths[:2], days=30)  # one young object is enough to keep the whole folder
    await reconcile(worker_db, storage, case)
    assert len(await storage.list(upload_prefix(case, folder))) == 3


async def test_a_folder_of_a_row_with_a_document_is_never_deleted(
    worker_db: Database, storage: CaseFileStorage, admin: psycopg.Connection, case: uuid.UUID, seed: Seed
) -> None:
    seed.document(case)
    done = add_upload(admin, case, "complete", document=True)
    paths = await put_chunks(storage, case, str(done))
    age_objects(paths, days=30)
    await reconcile(worker_db, storage, case)
    assert len(await storage.list(upload_prefix(case, done))) == 3


async def test_marked_cleaned_only_after_the_delete_succeeded(
    worker_db: Database,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    case: uuid.UUID,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cancelled = add_upload(admin, case, "cancelled")
    await put_chunks(storage, case, str(cancelled))

    async def refuse(_paths: object) -> None:
        raise StorageError("storage said 500")

    monkeypatch.setattr(storage, "delete", refuse)
    await reconcile(worker_db, storage, case)  # one folder failing must not raise
    assert admin.execute("select cleaned_at from app.uploads where id = %s", (cancelled,)).fetchone() == (None,)
    assert len(await storage.list(upload_prefix(case, cancelled))) == 3

    monkeypatch.undo()
    await reconcile(worker_db, storage, case)  # the next run finishes the job
    row = admin.execute("select cleaned_at is not null from app.uploads where id = %s", (cancelled,)).fetchone()
    assert row == (True,)
    assert await storage.list(upload_prefix(case, cancelled)) == []


async def test_logs_counts_only(
    worker_db: Database,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    case: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cancelled = add_upload(admin, case, "cancelled")
    await put_chunks(storage, case, str(cancelled))
    with caplog.at_level(logging.DEBUG, logger="app.core.housekeeping"):
        await reconcile(worker_db, storage, case)
    assert caplog.records
    assert str(case) not in caplog.text and str(cancelled) not in caplog.text and "uploads/" not in caplog.text


async def test_a_completed_documents_prefix_is_never_deleted_even_without_its_upload_row(
    worker_db: Database, storage: CaseFileStorage, admin: psycopg.Connection, case: uuid.UUID, seed: Seed
) -> None:
    doc = seed.document(case)
    folder = uuid.uuid4()  # the upload row is gone from view, but the document's bytes live at this path
    paths = await put_chunks(storage, case, str(folder))
    age_objects(paths, days=30)
    admin.execute("update app.documents set storage_path = %s where id = %s", (upload_prefix(case, folder), doc))
    await reconcile(worker_db, storage, case)
    assert len(await storage.list(upload_prefix(case, folder))) == 3


async def test_a_connection_not_scoped_to_the_case_deletes_nothing(
    worker_db: Database, storage: CaseFileStorage, admin: psycopg.Connection, case: uuid.UUID
) -> None:
    folder = uuid.uuid4()
    paths = await put_chunks(storage, case, str(folder))
    age_objects(paths, days=30)
    async with worker_db.anonymous() as conn:  # sees no rows at all: every folder would look row-less
        await reconcile_upload_objects(conn, storage, case)
    assert len(await storage.list(upload_prefix(case, folder))) == 3


async def test_the_api_role_deletes_nothing_even_with_the_case_setting_set(
    api_db: Database, storage: CaseFileStorage, admin: psycopg.Connection, case: uuid.UUID
) -> None:
    """`app.job_case_id` is a plain session setting any role can set (migration 0027), so the setting alone
    is no proof: on `app_api` every row stays hidden and each folder would look like an orphan."""
    folder = uuid.uuid4()
    paths = await put_chunks(storage, case, str(folder))
    age_objects(paths, days=30)
    async with api_db.anonymous() as conn:
        await conn.execute("select set_config('app.job_case_id', %s, true)", (str(case),))
        assert await reconcile_upload_objects(conn, storage, case) == ReconcileResult()
    assert len(await storage.list(upload_prefix(case, folder))) == 3


async def test_the_sweep_leaves_the_connection_unscoped_afterwards(
    worker_db: Database, storage: CaseFileStorage, admin: psycopg.Connection, case: uuid.UUID
) -> None:
    from datetime import date

    from app.core.housekeeping import sweep_uploads

    add_upload(admin, case)
    admin.execute("update app.uploads set expires_at = now() - interval '1 hour' where case_id = %s", (case,))
    async with worker_db.anonymous() as conn:
        await sweep_uploads(conn, date.today())
        row = await (await conn.execute("select app.current_job_case_id()")).fetchone()
    assert row == (None,)


class _Rows:
    def __init__(self, rows: list[tuple[object, ...]]) -> None:
        self.rows = rows

    async def fetchall(self) -> list[tuple[object, ...]]:
        return self.rows

    async def fetchone(self) -> tuple[object, ...] | None:
        return self.rows[0] if self.rows else None


class _Conn:
    async def execute(self, sql: str, params: object = ()) -> _Rows:
        if "current_job_case_id" in sql:
            return _Rows([(True,)])
        return _Rows([])

    def transaction(self) -> object:
        raise AssertionError("nothing to mark")


class _EmptyFolderStorage:
    def __init__(self, folder: uuid.UUID, times: dict[str, object]) -> None:
        self.folder, self.times = folder, times

    async def list_folders(self, _prefix: str) -> list[str]:
        return [str(self.folder)]

    async def list_object_times(self, _prefix: str) -> dict[str, object]:
        return self.times

    async def list_recursive(self, _prefix: str) -> list[str]:
        return []


async def test_a_row_less_folder_with_no_objects_is_not_counted_as_deleted(case: uuid.UUID) -> None:
    result = await reconcile_upload_objects(
        _Conn(),  # type: ignore[arg-type]
        _EmptyFolderStorage(uuid.uuid4(), {}),  # type: ignore[arg-type]
        case,
    )
    assert (result.deleted, result.failed) == (0, 0)


async def test_an_object_time_that_is_not_aware_utc_is_never_a_reason_to_delete(case: uuid.UUID) -> None:
    from datetime import datetime

    store = _EmptyFolderStorage(uuid.uuid4(), {"x": datetime(2020, 1, 1)})  # naive: Storage sent no offset
    result = await reconcile_upload_objects(_Conn(), store, case)  # type: ignore[arg-type]
    assert result.left == 1


async def test_reconciling_logs_the_upload_reconciled_event_with_counts_only(
    worker_db: Database,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    case: uuid.UUID,
    caplog: pytest.LogCaptureFixture,
) -> None:
    cancelled = add_upload(admin, case, "cancelled")
    await put_chunks(storage, case, str(cancelled))
    with caplog.at_level(logging.INFO, logger="app.core.housekeeping"):
        await reconcile(worker_db, storage, case)
    (event,) = [r for r in caplog.records if r.getMessage() == "upload.reconciled"]
    assert {event.deleted, event.left, event.failed} <= set(range(0, 100))  # type: ignore[attr-defined]
    assert hasattr(event, "deleted") and hasattr(event, "left") and hasattr(event, "failed")
