"""AUDIT-12 (file lifecycle): deleting a case deletes every copy, in a safe order, and nothing lets a copy back in.

D1 runs the real `delete_case` job on a case that holds one of everything (a finalised upload and its document,
page image, thumbnail and one-page PDF, a report file, an export, an open upload and a cancelled upload whose
parts the daily sweep has not reached, and a row in every case table) and then looks for the case, by id and by
a marker, in every object of the bucket and every table of schemas `app` and `procrastinate`.
D2 breaks Storage half way. D3 asks who can still reach the files while a deletion is queued and after it ran.
D4 sends parts and a queued `finalize_upload` job at a case that has just been deleted. D6 covers the other
deleters. D5 (replaying the deletion ledger after a restore) is already covered by
`tests/guards/test_foundation_case_purge.py::test_replaying_the_ledger_removes_a_case_a_restore_brought_back`.

Each test asserts the safe behaviour and shows first that the thing existed (a "gone" is read against a "was
there"). Where the application behaves unsafely the safe assertion is kept and marked `xfail(strict=True)`.
Everything is made up; every plaintext value the product writes carries `AUDIT12-MARKER-<uuid>`.
"""

from __future__ import annotations

import io
import uuid
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
import psycopg
import pytest
from botocore.exceptions import EndpointConnectionError
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, CaseKeyDestroyed, LocalKeyProvider
from app.core.db import Database
from app.core.email import OutboxMailer
from app.core.jobs import JobContext
from app.core.storage import CaseFileStorage, StorageError, upload_prefix
from app.core.uploads import finalize
from app.features.close import jobs as close_jobs
from app.features.close import sealed
from app.features.close.service import export_path
from tests.support.db import ClientFor
from tests.support.file_lifecycle_deletion import (
    BY_DESIGN,
    Lifecycle,
    add_stored_copies,
    bucket_keys,
    case_id_tables,
    fill_and_plant,
    make_due,
    marker_tables,
    seed_people,
    status_of,
    sweep,
    tiny_file,
)
from tests.support.files import build_app, local_storage
from tests.support.seed import Seed
from tests.support.uploads import complete, put, start

pytestmark = pytest.mark.anyio

Run = Callable[..., Awaitable[OutboxMailer]]

# --- fixtures (as tests/repository/test_close_jobs.py) ------------------------------------------------------


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
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture(autouse=True)
def services(monkeypatch: pytest.MonkeyPatch, storage: CaseFileStorage, crypto: CaseCrypto) -> None:
    monkeypatch.setattr(close_jobs.services, "storage", storage)
    monkeypatch.setattr(close_jobs.services, "crypto", crypto)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage, crypto: CaseCrypto) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=crypto)


@pytest.fixture
def run(worker_db: Database, seed: Seed) -> Run:
    """`await run(handler, case, kind, job=, attempt=)`: one attempt of a job on the case, as the worker would."""

    async def go(
        handler: Callable[[JobContext], Awaitable[None]],
        case: uuid.UUID,
        kind: str,
        *,
        job: uuid.UUID | None = None,
        attempt: int = 1,
    ) -> OutboxMailer:
        mailer = OutboxMailer()
        job = job or seed.job(case, kind=kind)
        await handler(JobContext(worker_db, job, case, kind, attempt, mailer=mailer))
        return mailer

    return go


async def make_export(life: Lifecycle, who: uuid.UUID, seed: Seed, admin: psycopg.Connection, run: Run) -> uuid.UUID:
    """A finished export of the case that `who` asked for (the named expert gets `full`, anyone else `admin`)."""
    # the job row is `queued` as seeded; `export_case` itself does not finish it (the worker does)
    job = seed.job(life.case, kind="export_case", requested_by=who)
    await run(close_jobs.export_case, life.case, "export_case", job=job)
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    return job


@pytest.fixture
async def life(
    seed: Seed,
    admin: psycopg.Connection,
    client_for: ClientFor,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    run: Run,
) -> Lifecycle:
    from tests.support.file_lifecycle_deletion import new_marker

    world = seed_people(seed, new_marker())
    expert_client = await client_for(world.expert)
    await add_stored_copies(
        world, seed=seed, admin=admin, client=expert_client, storage=storage, crypto=crypto, worker_db=worker_db
    )
    # The administrative copy: the named expert's "full" copy cannot be built for a real upload (see test_d6c).
    world.export_job = await make_export(world, world.org_admin, seed, admin, run)
    world.objects[export_path(world.case, world.export_job, "admin")] = "export"
    return world


def part_path(case: uuid.UUID, upload: str) -> str:
    return f"{upload_prefix(case, upload)}/00001"


async def read_status(client: httpx.AsyncClient, method: str, url: str, **kwargs: Any) -> int:
    return (await client.request(method, url, **kwargs)).status_code


async def shown_to_exist(life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection) -> list[str]:
    """Show (before a deletion) that every kind of stored copy is there; return the paths under the prefix."""
    stored = await storage.list_recursive(life.prefix)
    for path, label in life.objects.items():
        assert path in stored, f"the {label} was never stored"
    for upload in (life.done_upload, life.open_upload, life.cancelled_upload):
        assert part_path(life.case, upload) in stored, "an upload's part was never stored"
    cancelled = admin.execute("select status, cleaned_at from app.uploads where id = %s", (life.cancelled_upload,))
    assert cancelled.fetchone() == ("cancelled", None), "the cancelled upload's parts were already swept"
    return stored


# =============================================================================================================
# D1 full deletion, every copy
# =============================================================================================================


async def test_d1a_no_object_of_the_case_is_left_in_the_bucket_after_deletion(
    life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection, run: Run, client_for: ClientFor
) -> None:
    stored = await shown_to_exist(life, storage, admin)
    expert = await client_for(life.expert)
    got = await expert.get(f"/v1/cases/{life.case}/documents/{life.document}/file")
    assert got.status_code == 200 and got.content == life.original, "positive control: the file is served"
    assert len(stored) >= 8
    named = bucket_keys(str(life.case)) + bucket_keys(life.marker)
    assert sorted(set(named)) == stored, "every object naming the case is under its prefix before deletion"

    make_due(admin, life.case)
    await run(close_jobs.delete_case, life.case, "delete_case")

    assert await storage.list_recursive(life.prefix) == []
    left = bucket_keys(str(life.case)) + bucket_keys(life.marker)
    assert left == [], "an object outside or inside the prefix still names the case or the marker"


async def test_d1b_the_key_is_destroyed_and_an_old_blob_can_no_longer_be_opened(
    life: Lifecycle,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    admin: psycopg.Connection,
    run: Run,
) -> None:
    path = part_path(life.case, life.done_upload)
    backup = await storage.get(path)  # the copy a backup would hold
    assert life.original not in backup
    async with worker_db.job(life.case) as conn:
        assert await crypto.decrypt(conn, life.case, backup, purpose=path) == life.original, "positive control"
    live = admin.execute(
        "select wrapped_key is not null, destroyed_at is null from app.case_keys where case_id = %s", (life.case,)
    ).fetchone()
    assert live == (True, True)

    make_due(admin, life.case)
    await run(close_jobs.delete_case, life.case, "delete_case")

    fresh = CaseCrypto(crypto.provider)  # another process: nothing cached
    for reader in (crypto, fresh):
        async with worker_db.job(life.case) as conn:
            with pytest.raises(CaseKeyDestroyed):
                await reader.decrypt(conn, life.case, backup, purpose=path)
    async with worker_db.job(life.case) as conn:
        with pytest.raises(CaseKeyDestroyed):  # and nothing quietly makes a new key for the case
            await fresh.encrypt(conn, life.case, b"new", purpose="x")
    kept = admin.execute(
        "select wrapped_key, destroyed_at is not null from app.case_keys where case_id = %s", (life.case,)
    ).fetchone()
    assert kept == (None, True)


async def test_d1g_a_sealed_export_cannot_be_opened_once_the_key_is_destroyed(
    life: Lifecycle,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    admin: psycopg.Connection,
    run: Run,
) -> None:
    path = export_path(life.case, life.export_job, "admin")  # type: ignore[arg-type]
    backup = await storage.get(path)  # the stored copy a backup would hold
    assert backup.startswith(sealed.MAGIC) and b"PK\x03\x04" not in backup[:200], "stored sealed, not as a ZIP"
    async with worker_db.job(life.case) as conn:
        opened = await sealed.open_sealed(conn, crypto, life.case, backup, purpose=path)
    assert opened[:2] == b"PK", "positive control: with the key it opens as the ZIP"

    make_due(admin, life.case)
    await run(close_jobs.delete_case, life.case, "delete_case")

    fresh = CaseCrypto(crypto.provider)  # another process: nothing cached
    async with worker_db.job(life.case) as conn:
        with pytest.raises(CaseKeyDestroyed):
            await sealed.open_sealed(conn, fresh, life.case, backup, purpose=path)


async def test_d1c_no_plaintext_marker_survives_outside_the_tables_kept_by_design(
    life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection, seed: Seed, run: Run
) -> None:
    await shown_to_exist(life, storage, admin)
    before = sweep(admin, life.case, life.marker)
    assert {"cases", "uploads", "documents"} <= marker_tables(before), "positive control: the marker is in the rows"
    fill_and_plant(admin, seed, life)
    assert len(life.planted) >= 20, "the marker was put in a column of many case tables"
    before = sweep(admin, life.case, life.marker)
    assert len(marker_tables(before)) >= 20

    await run(close_jobs.delete_case, life.case, "delete_case")

    after = sweep(admin, life.case, life.marker)
    survivors = sorted(marker_tables(after))
    assert survivors == [], f"rows still holding plaintext of the case: {survivors}"


async def test_d1d_no_case_id_survives_outside_the_tables_kept_by_design(
    life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection, seed: Seed, run: Run
) -> None:
    await shown_to_exist(life, storage, admin)
    fill_and_plant(admin, seed, life)
    before = sweep(admin, life.case, life.marker)
    assert len(case_id_tables(before)) >= 30, "positive control: the case id is in many tables"
    assert "procrastinate_jobs" in case_id_tables(before), "positive control: the queue holds the case's jobs"

    await run(close_jobs.delete_case, life.case, "delete_case")

    after = sweep(admin, life.case, life.marker)
    survivors = sorted(case_id_tables(after) - BY_DESIGN)
    # the queue's rows are their own finding (test_d1f); nothing else may name the case
    assert set(survivors) <= {"procrastinate_jobs"}, f"tables still naming the case: {survivors}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 D1f info: "
        "procrastinate_jobs keep the case id and job id (args) and procrastinate_events the job ids for"
        " ever; nothing purges them (ids only, no content): api/app/core/jobs.py, "
        "supabase/migrations/0046_foundation_bundle_purge.sql"
    ),
)
async def test_d1f_the_queue_keeps_no_row_of_the_case_after_deletion(
    life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection, run: Run
) -> None:
    await shown_to_exist(life, storage, admin)
    queued = [
        r[0]
        for r in admin.execute(
            "select id from procrastinate.procrastinate_jobs where args->>'case_id' = %s", (str(life.case),)
        ).fetchall()
    ]
    assert queued, "positive control: the queue holds the case's jobs"
    events = admin.execute(
        "select count(*) from procrastinate.procrastinate_events where job_id = any(%s)", (queued,)
    ).fetchone()
    assert events is not None and events[0] > 0, "positive control: the queue holds events for them"
    make_due(admin, life.case)

    await run(close_jobs.delete_case, life.case, "delete_case")

    left = admin.execute(
        "select (select count(*) from procrastinate.procrastinate_jobs where id = any(%s)),"
        " (select count(*) from procrastinate.procrastinate_events where job_id = any(%s))",
        (queued, queued),
    ).fetchone()
    assert left == (0, 0), f"queue rows left (jobs, events): {left}"


async def test_d1e_the_certificate_and_its_email_hold_no_name_but_are_given_to_the_right_people(
    life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection, run: Run, client_for: ClientFor
) -> None:
    await shown_to_exist(life, storage, admin)
    expert, org_admin = await client_for(life.expert), await client_for(life.org_admin)
    others = [await client_for(p) for p in (life.assistant, life.viewer, life.outsider)]
    url = f"/v1/cases/{life.case}/deletion-certificate"
    assert (await expert.get(url)).status_code == 404, "positive control: nothing to show before deletion"

    admin.execute("delete from app.deletion_certificates where case_id = %s", (life.case,))
    make_due(admin, life.case)
    mailer = await run(close_jobs.delete_case, life.case, "delete_case")

    body = await expert.get(url)
    assert body.status_code == 200 and (await org_admin.get(url)).status_code == 200
    pdf = await expert.get(url, params={"format": "pdf"})
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert [r.status_code for r in [await c.get(url) for c in others]] == [404, 404, 404]
    text = body.text + repr(mailer.outbox)
    assert life.marker not in text and life.marker.encode() not in pdf.content
    assert mailer.outbox, "positive control: the certificate was mailed"
    assert str(life.case) in body.text


# =============================================================================================================
# D2 order and failure
# =============================================================================================================


def key_destroyed(admin: psycopg.Connection, case: uuid.UUID) -> bool:
    row = admin.execute("select destroyed_at is not null from app.case_keys where case_id = %s", (case,)).fetchone()
    return bool(row and row[0])


def certificate_state(admin: psycopg.Connection, case: uuid.UUID) -> tuple[int, bool]:
    """(certificate rows, whether the certificate says the deletion finished)."""
    row = admin.execute(
        "select count(*), coalesce(bool_or(deleted_at is not null), false) from app.deletion_certificates"
        " where case_id = %s",
        (case,),
    ).fetchone()
    assert row is not None
    return int(row[0]), bool(row[1])


async def test_d2a_when_storage_is_down_nothing_is_destroyed_and_a_retry_finishes_once(
    life: Lifecycle,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    admin: psycopg.Connection,
    run: Run,
    client_for: ClientFor,
    monkeypatch: pytest.MonkeyPatch,
    seed: Seed,
) -> None:
    stored = await shown_to_exist(life, storage, admin)
    admin.execute("delete from app.deletion_certificates where case_id = %s", (life.case,))
    make_due(admin, life.case)
    job = seed.job(life.case, kind="delete_case")
    real = storage._client.delete_objects

    def down(**kwargs: Any) -> Any:
        raise EndpointConnectionError(endpoint_url="http://storage.invalid")

    monkeypatch.setattr(storage._client, "delete_objects", down)
    with pytest.raises(StorageError):
        await run(close_jobs.delete_case, life.case, "delete_case", job=job)

    assert await storage.list_recursive(life.prefix) == stored, "no object was removed"
    assert not key_destroyed(admin, life.case), "the key is still alive, so the objects are still readable"
    async with worker_db.job(life.case) as conn:
        await CaseCrypto(crypto.provider).warm(conn, life.case)
    assert status_of(admin, life.case) == "settled", "no row was purged"
    assert certificate_state(admin, life.case) == (1, False), "the certificate says deletion started, not finished"
    expert = await client_for(life.expert)
    got = await expert.get(f"/v1/cases/{life.case}/documents/{life.document}/file")
    assert got.status_code == 200, "the expert can still read the case: nothing was half-deleted"

    monkeypatch.setattr(storage._client, "delete_objects", real)
    await run(close_jobs.delete_case, life.case, "delete_case", job=job, attempt=2)
    assert await storage.list_recursive(life.prefix) == []
    assert key_destroyed(admin, life.case) and status_of(admin, life.case) == "deleted"
    assert certificate_state(admin, life.case) == (1, True), "exactly one certificate, finished"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 D2b medium: "
        "storage.delete sends DeleteObjects with Quiet=True and never reads the Errors it answers with, "
        "so a refused key is taken for deleted; the key is then destroyed and the certificate finished with an object "
        "left: api/app/core/storage.py:380"
    ),
)
async def test_d2b_a_partial_storage_failure_is_not_taken_for_success(
    life: Lifecycle,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    admin: psycopg.Connection,
    run: Run,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S3's `DeleteObjects` answers 200 and lists the keys it could not remove under `Errors`.

    The stand-in is told to fail for one key; the others go. Safe: the run does not go on to destroy the key,
    purge the rows or write a finished certificate while an object is left.
    """
    stored = await shown_to_exist(life, storage, admin)
    admin.execute("delete from app.deletion_certificates where case_id = %s", (life.case,))
    make_due(admin, life.case)
    sticky = part_path(life.case, life.done_upload)
    real = storage._client.delete_objects
    calls: list[int] = []

    def partial(**kwargs: Any) -> Any:
        keys = [o["Key"] for o in kwargs["Delete"]["Objects"]]
        calls.append(len(keys))
        rest = [{"Key": k} for k in keys if k != sticky]
        if rest:
            real(Bucket=kwargs["Bucket"], Delete={"Objects": rest, "Quiet": True})
        return {"Errors": [{"Key": sticky, "Code": "InternalError", "Message": "Please try again."}]}

    monkeypatch.setattr(storage._client, "delete_objects", partial)
    try:
        await run(close_jobs.delete_case, life.case, "delete_case")
    except Exception:  # noqa: BLE001 - raising is the safe outcome; what matters is the state left behind
        pass

    assert calls and sum(calls) == len(stored), "the stand-in was asked to delete every object once"
    left = await storage.list_recursive(life.prefix)
    assert left, "the stand-in kept an object, as it said"
    finished = certificate_state(admin, life.case)[1]
    assert not key_destroyed(admin, life.case) and not finished and status_of(admin, life.case) != "deleted", (
        f"left: {len(left)} object(s); key destroyed: {key_destroyed(admin, life.case)};"
        f" certificate finished: {finished}; case status: {status_of(admin, life.case)}"
    )


# =============================================================================================================
# D3 access while deletion is pending, and after
# =============================================================================================================

PEOPLE = ("named_expert", "assistant", "viewer", "org_admin", "outsider")


async def content_status(clients: dict[str, httpx.AsyncClient], life: Lifecycle) -> dict[str, dict[str, int]]:
    """Each person's status for each content route (None-safe: a status for every pair)."""
    case = life.case
    out: dict[str, dict[str, int]] = {}
    for who, client in clients.items():
        out[who] = {
            "document file": await read_status(client, "GET", f"/v1/cases/{case}/documents/{life.document}/file"),
            "page image": await read_status(client, "GET", f"/v1/cases/{case}/pages/{life.page_no}/image"),
            "page image (small)": await read_status(
                client, "GET", f"/v1/cases/{case}/pages/{life.page_no}/image?size=small"
            ),
            "thumbnail": await read_status(client, "GET", f"/v1/cases/{case}/pages/{life.page_no}/thumbnail"),
        }
    return out


@pytest.fixture
async def clients(life: Lifecycle, client_for: ClientFor) -> dict[str, httpx.AsyncClient]:
    return {name: await client_for(person) for name, person in life.people().items()}


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 POLICY D3: state-machines.md Deletion is silent on a queued deletion; every role that may view still "
        "gets the document file and page images until the job runs: api/app/features/close/jobs.py:156 (access is "
        "refused only once status is deleted)"
    ),
)
async def test_d3a_while_a_deletion_is_queued_nobody_can_still_fetch_a_file(
    life: Lifecycle, clients: dict[str, httpx.AsyncClient], seed: Seed, admin: psycopg.Connection
) -> None:
    """AUDIT-12 POLICY D3: state-machines.md "Deletion" is silent on a queued deletion; a case stays open until it
    runs."""
    before = await content_status(clients, life)
    assert before["named_expert"]["document file"] == 200, "positive control: the expert gets the stored file"
    assert before["named_expert"]["page image"] == 200 and before["named_expert"]["thumbnail"] == 200
    make_due(admin, life.case)
    seed.job(life.case, kind="delete_case")  # queued, not run
    after = await content_status(clients, life)
    open_now = {
        who: sorted(route for route, code in routes.items() if code < 300 and before[who][route] < 300)
        for who, routes in after.items()
    }
    assert {w: r for w, r in open_now.items() if r} == {}, "files still served while the deletion is queued"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 POLICY D3: no rule stops a new upload on a case whose deletion is queued; the named expert and an "
        "assistant get 201: api/app/core/routes/uploads.py:165"
    ),
)
async def test_d3b_while_a_deletion_is_queued_nobody_can_start_a_new_upload(
    life: Lifecycle, clients: dict[str, httpx.AsyncClient], seed: Seed, admin: psycopg.Connection
) -> None:
    """AUDIT-12 POLICY D3: no written rule stops a new upload on a case whose deletion is queued."""
    body = {"kind": "records", "filename": f"{life.marker}-late.pdf", "size": 300, "sha256": "0" * 64}
    url = f"/v1/cases/{life.case}/uploads"
    first = await clients["named_expert"].post(url, json=body)
    assert first.status_code == 201, "positive control: the expert may add a file before the deletion is queued"
    make_due(admin, life.case)
    seed.job(life.case, kind="delete_case")
    statuses = {who: (await c.post(url, json=body)).status_code for who, c in clients.items() if who != "outsider"}
    assert [who for who, code in statuses.items() if code < 300] == [], f"uploads accepted: {statuses}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 POLICY D3: an export is downloadable while the deletion is queued (it is the way out before the case "
        "goes): api/app/features/close/service.py:276"
    ),
)
async def test_d3c_while_a_deletion_is_queued_an_export_cannot_be_downloaded(
    life: Lifecycle, clients: dict[str, httpx.AsyncClient], seed: Seed, admin: psycopg.Connection, run: Run
) -> None:
    """AUDIT-12 POLICY D3: an export is built and served on a case whose deletion is queued (it is the way out)."""
    control = await make_export(life, life.org_admin, seed, admin, run)
    pending = await make_export(life, life.org_admin, seed, admin, run)
    url = "/v1/cases/{}/export/{}/download"
    got = await clients["org_admin"].get(url.format(life.case, control))
    assert got.status_code == 200 and got.content[:2] == b"PK", "positive control: an export downloads"
    make_due(admin, life.case)
    seed.job(life.case, kind="delete_case")
    after = await clients["org_admin"].get(url.format(life.case, pending))
    assert after.status_code >= 400, "an export was downloaded while the deletion was queued"


async def test_d3d_after_deletion_every_content_route_is_404_for_everyone_and_the_certificate_is_not(
    life: Lifecycle, clients: dict[str, httpx.AsyncClient], admin: psycopg.Connection, run: Run, seed: Seed
) -> None:
    before = await content_status(clients, life)
    assert before["named_expert"] == {
        "document file": 200,
        "page image": 200,
        "page image (small)": 200,
        "thumbnail": 200,
    }
    upload_body = {"kind": "records", "filename": "x.pdf", "size": 300, "sha256": "0" * 64}
    upload_url = f"/v1/cases/{life.case}/uploads"
    export = await make_export(life, life.org_admin, seed, admin, run)
    export_url = f"/v1/cases/{life.case}/export/{export}/download"
    cert_url = f"/v1/cases/{life.case}/deletion-certificate"
    assert (await clients["org_admin"].get(export_url)).status_code == 200, "positive control: export"
    assert (await clients["named_expert"].post(upload_url, json=upload_body)).status_code == 201, "positive control"
    admin.execute("delete from app.deletion_certificates where case_id = %s", (life.case,))
    make_due(admin, life.case)

    await run(close_jobs.delete_case, life.case, "delete_case")

    after = await content_status(clients, life)
    assert {w: set(r.values()) for w, r in after.items()} == {w: {404} for w in clients}
    for who, client in clients.items():
        assert (await client.post(upload_url, json=upload_body)).status_code == 404, who
        assert (await client.get(export_url)).status_code == 404, who
        assert (await client.post(f"/v1/cases/{life.case}/export", json={})).status_code == 404, who
        assert (await client.get(f"/v1/cases/{life.case}/pages/{life.page_no}/facts")).status_code == 404, who
    certificate = {who: (await c.get(cert_url)).status_code for who, c in clients.items()}
    assert certificate == {
        "named_expert": 200,
        "org_admin": 200,
        "assistant": 404,
        "viewer": 404,
        "outsider": 404,
    }


# =============================================================================================================
# D4 uploads against deletion
# =============================================================================================================


async def test_d4a_after_deletion_parts_completions_and_new_uploads_are_refused_and_store_nothing(
    life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection, run: Run, client_for: ClientFor
) -> None:
    expert = await client_for(life.expert)
    extra = tiny_file(life.marker, 44)
    upload = await start(expert, life.case, extra, filename=f"{life.marker}-race.pdf")
    assert (await put(expert, upload, 1, extra.chunk(1))).status_code == 200, "positive control: a part is taken"
    assert part_path(life.case, upload) in await storage.list_recursive(life.prefix)
    open_part = tiny_file(life.marker, 42)

    make_due(admin, life.case)
    await run(close_jobs.delete_case, life.case, "delete_case")

    assert (await put(expert, upload, 1, extra.chunk(1))).status_code == 404
    assert (await put(expert, life.open_upload, 1, open_part.chunk(1))).status_code == 404
    assert (await complete(expert, upload, extra.sha256())).status_code == 404
    assert (await expert.post(f"/v1/uploads/{life.open_upload}/cancel")).status_code == 404
    fresh = await expert.post(
        f"/v1/cases/{life.case}/uploads",
        json={"kind": "records", "filename": "x.pdf", "size": 300, "sha256": "0" * 64},
    )
    assert fresh.status_code == 404
    assert bucket_keys(str(life.case)) == [], "an object was written for the deleted case"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 D4b low: "
        "put_chunk writes the sealed part to Storage before it takes the upload lock and re-checks, so a "
        "part racing a deletion is refused (404) but its object stays under the deleted case's prefix: "
        "api/app/core/routes/uploads.py:292"
    ),
)
async def test_d4b_a_part_in_flight_when_the_deletion_runs_leaves_no_object_behind(
    life: Lifecycle,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    run: Run,
    client_for: ClientFor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The deletion runs between the part's access check and its write to Storage (`put_chunk` stores first, then
    takes the upload's lock and looks again). Safe: the part is refused and nothing is left under the case's prefix."""
    expert = await client_for(life.expert)
    racing = tiny_file(life.marker, 45)
    upload = await start(expert, life.case, racing, filename=f"{life.marker}-flight.pdf")
    await shown_to_exist(life, storage, admin)
    real_put = storage.put
    ran: list[bool] = []

    async def deletion_first(path: str, data: bytes) -> None:
        if not ran and path == part_path(life.case, upload):
            ran.append(True)
            await run(close_jobs.delete_case, life.case, "delete_case")
        await real_put(path, data)

    make_due(admin, life.case)
    monkeypatch.setattr(storage, "put", deletion_first)
    answer = await put(expert, upload, 1, racing.chunk(1))

    assert ran == [True], "the deletion ran in the middle of the part"
    assert answer.status_code == 404, answer.text
    assert bucket_keys(str(life.case)) == [], "the part's object was written after the case was deleted"


async def test_d4c_a_finalize_job_left_in_the_queue_does_not_bring_back_a_document_or_objects(
    life: Lifecycle,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    admin: psycopg.Connection,
    run: Run,
    client_for: ClientFor,
) -> None:
    expert = await client_for(life.expert)
    late = tiny_file(life.marker, 46)
    upload = await start(expert, life.case, late, filename=f"{life.marker}-late.pdf")
    assert (await put(expert, upload, 1, late.chunk(1))).status_code == 200
    assert (await complete(expert, upload, late.sha256())).status_code == 202
    found = admin.execute("select finalize_job_id, status from app.uploads where id = %s", (upload,)).fetchone()
    assert found is not None and found[1] == "finalizing", "positive control: a finalize job is waiting"
    job = found[0]
    queued = admin.execute(
        "select count(*) from procrastinate.procrastinate_jobs where args->>'job_id' = %s", (str(job),)
    ).fetchone()
    assert queued == (1,), "the queue holds the job, as a worker would find it"

    make_due(admin, life.case)
    await run(close_jobs.delete_case, life.case, "delete_case")

    documents_before = admin.execute(
        "select count(*) from app.documents where storage_path like %s", (f"%{life.case}%",)
    ).fetchone()
    for reader in (crypto, CaseCrypto(crypto.provider)):  # the worker that holds a key, and one that does not
        await finalize(JobContext(worker_db, job, life.case, "finalize_upload", 1), storage, reader)
    assert documents_before == (0,)
    assert admin.execute(
        "select count(*) from app.documents where storage_path like %s", (f"%{life.case}%",)
    ).fetchone() == (0,)
    assert admin.execute("select count(*) from app.uploads where case_id = %s", (life.case,)).fetchone() == (0,)
    assert bucket_keys(str(life.case)) == []
    assert status_of(admin, life.case) == "deleted"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 D4d low: "
        "after a failed key step the case still takes parts, and the retry skips the finished storage "
        "step, so a part sent in the gap survives the deletion (sealed under a key that is then destroyed): "
        "api/app/features/close/jobs.py:206"
    ),
)
async def test_d4d_an_upload_made_while_a_failed_deletion_waits_to_retry_is_removed_by_the_retry(
    life: Lifecycle,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    admin: psycopg.Connection,
    run: Run,
    client_for: ClientFor,
    monkeypatch: pytest.MonkeyPatch,
    seed: Seed,
) -> None:
    """The run is resumable: Storage is emptied (step done), then the key step fails and the case is still open.
    A part sent in that gap is stored under the prefix; the retry skips the finished storage step."""
    expert = await client_for(life.expert)
    admin.execute("delete from app.deletion_certificates where case_id = %s", (life.case,))
    make_due(admin, life.case)
    job = seed.job(life.case, kind="delete_case")
    real_destroy = crypto.destroy
    failed: list[bool] = []

    async def failing_once(conn: Any, case: uuid.UUID) -> None:
        if not failed:
            failed.append(True)
            raise RuntimeError("the key service did not answer")
        await real_destroy(conn, case)

    monkeypatch.setattr(crypto, "destroy", failing_once)
    with pytest.raises(RuntimeError):
        await run(close_jobs.delete_case, life.case, "delete_case", job=job)
    assert await storage.list_recursive(life.prefix) == [], "positive control: Storage was emptied first"
    late = tiny_file(life.marker, 47)
    upload = await start(expert, life.case, late, filename=f"{life.marker}-gap.pdf")
    sent = await put(expert, upload, 1, late.chunk(1))
    assert sent.status_code == 200, "positive control: the case still takes a part while the deletion is half done"
    assert part_path(life.case, upload) in await storage.list_recursive(life.prefix)

    await run(close_jobs.delete_case, life.case, "delete_case", job=job, attempt=2)

    assert status_of(admin, life.case) == "deleted"
    assert await storage.list_recursive(life.prefix) == [], "the part sent during the gap survived the retry"


# =============================================================================================================
# D6 other deleters
# =============================================================================================================


@pytest.fixture
async def open_life(
    seed: Seed,
    admin: psycopg.Connection,
    client_for: ClientFor,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
) -> Lifecycle:
    """The same copies on an active case (a settled case takes no changes to its documents)."""
    from tests.support.file_lifecycle_deletion import new_marker

    world = seed_people(seed, new_marker(), status="active")
    await add_stored_copies(
        world,
        seed=seed,
        admin=admin,
        client=await client_for(world.expert),
        storage=storage,
        crypto=crypto,
        worker_db=worker_db,
    )
    return world


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 D6a low: remove-one-document is a broken feature that fails closed (nothing is deleted, no copy is"
        " exposed): DELETE /v1/cases/{id}/documents/{doc} answers 404 to the named expert because app_api has no"
        " DELETE policy on app.documents (supabase/migrations/0042_foundation_bundle_rls.sql:36-42), so"
        " repo.delete_document matches no row: api/app/features/pipeline/repo.py:1239"
    ),
)
async def test_d6a_removing_one_document_removes_its_files_and_every_trace_of_its_name(
    open_life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection, client_for: ClientFor
) -> None:
    life = open_life
    stored = await storage.list_recursive(life.prefix)
    assert part_path(life.case, life.done_upload) in stored and life.page_id is not None
    done_marker = f"{life.marker}-done"
    hits = sweep(admin, life.case, done_marker)
    assert {"documents", "uploads"} <= marker_tables(hits), "positive control: the file name is in the rows"
    expert = await client_for(life.expert)

    removed = await expert.delete(f"/v1/cases/{life.case}/documents/{life.document}")
    assert removed.status_code == 204, removed.text

    left = await storage.list_recursive(life.prefix)
    assert part_path(life.case, life.done_upload) not in left, "the document's stored parts are gone"
    page_files = [k for k, v in life.objects.items() if v in {"page image", "thumbnail", "page pdf"}]
    assert page_files and not [p for p in page_files if p in left], "its page files are gone"
    assert part_path(life.case, life.open_upload) in left, "another upload's parts are not touched"
    assert sorted(marker_tables(sweep(admin, life.case, done_marker))) == [], "its file name is still in a row"


async def test_d6b_an_export_can_be_taken_once_and_its_stored_copy_goes_with_it(
    life: Lifecycle, storage: CaseFileStorage, admin: psycopg.Connection, client_for: ClientFor
) -> None:
    await shown_to_exist(life, storage, admin)
    path = export_path(life.case, life.export_job, "admin")  # type: ignore[arg-type]
    assert path in await storage.list_recursive(life.prefix), "positive control: the stored copy exists"
    admin_client = await client_for(life.org_admin)
    url = f"/v1/cases/{life.case}/export/{life.export_job}/download"
    first = await admin_client.get(url)
    assert first.status_code == 200 and first.content[:2] == b"PK", "positive control"
    assert path not in await storage.list_recursive(life.prefix)
    assert (await admin_client.get(url)).status_code == 404


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-12 D6c high: export_case reads documents.storage_path with storage.get, but an uploaded document's "
        "storage_path is the upload prefix (its parts are below it), so the named expert's export fails NoSuchKey: "
        "api/app/features/close/jobs.py:397 vs api/app/core/uploads.py:179"
    ),
)
async def test_d6c_the_named_experts_export_builds_for_a_case_with_an_uploaded_document(
    life: Lifecycle,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    run: Run,
    seed: Seed,
    client_for: ClientFor,
) -> None:
    """The export is the expert's way out before a deletion. The "full" copy reads each document at
    `documents.storage_path`, which for an uploaded document is the upload's prefix, not an object."""
    stored = await shown_to_exist(life, storage, admin)
    assert any(p.startswith(upload_prefix(life.case, life.done_upload)) for p in stored)
    job = await make_export(life, life.expert, seed, admin, run)
    assert export_path(life.case, job, "full") in await storage.list_recursive(life.prefix)
    # and the expert can take it: the ZIP holds the bytes that were uploaded (the original, exactly)
    expert = await client_for(life.expert)
    got = await expert.get(f"/v1/cases/{life.case}/export/{job}/download")
    assert got.status_code == 200 and got.content[:2] == b"PK", got.text
    with zipfile.ZipFile(io.BytesIO(got.content)) as archive:
        carried = [archive.read(n) for n in archive.namelist() if n.startswith("bundle/")]
    assert life.original in carried, "the uploaded document is not in the export"
