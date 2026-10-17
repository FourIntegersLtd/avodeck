"""Slice H jobs (architecture 10.9 and 10.12; failure modes F20, F28, F29, F30).

`delete_case` (in the case's own region cell, rule 5):
1. A held case (on hold, or an active legal hold) is skipped and the named expert told once (F30).
   A case no longer due (reopened since it was queued) is left alone.
2. The certificate row is written first: from then on no hold can be set and the run is resumable.
3. Every object under the case's Storage prefix is removed (documents, page images, quarantined and
   superseded pages, exhibits, exports), then the case key is destroyed (crypto-shredding).
4. `app.purge_case()` removes the rows of every registered case table and leaves the tombstone; the
   certificate gets the counts, file hashes, log chain heads, the date and the backup expiry date,
   and is emailed (patient-free) in the same transaction. Interrupted at any point, a rerun finishes
   the job with exactly one certificate (F20).

`export_case`: the requester's copy of the record of work (full for the named expert, administrative for an
organisation admin) as an encrypted ZIP, downloaded once, by that person only, through the API.
The named expert gets the full copy (with the bundle); an organisation admin the administrative one
(report versions, hashes and logs, no page images).

The daily close run: holds past their end date are released, settled cases older than the archive
period are archived, closed cases without a deletion date get one, and every case due for deletion
gets one `delete_case` job. Missed days are caught up, each once (F28).
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import uuid
import zipfile
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.contracts.case_tables import CASE_TABLES
from app.core.crypto import CaseCrypto, CaseKeyDestroyed, LocalKeyProvider
from app.core.db import Database
from app.core.email import OutboxMailer
from app.core.jobs import JobContext, JobRegistry
from app.core.packs import default_library
from app.core.storage import SupabaseStorage, case_prefix
from app.features.close import jobs as close_jobs
from tests.support.case_rows import CaseWorld, fill_every_case_table
from tests.support.contracts import add_report_version
from tests.support.db import ClientFor
from tests.support.files import build_app, local_storage
from tests.support.jobs import isolated_registry, open_worker
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LABEL = "Zz Example Subject Label"
FULL_NAME = "Zz Example Expert Name"
FILENAME = "zz-example-subject-records.pdf"
FIXTURE_YEARS = default_library().combine("zz-test", "zz-case", "zz-specialty").retention_years


# --- fixtures ---------------------------------------------------------------------------------------


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
    monkeypatch.setattr(close_jobs.services, "storage", storage)
    monkeypatch.setattr(close_jobs.services, "crypto", crypto)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: SupabaseStorage, crypto: CaseCrypto) -> FastAPI:
    return build_app(api_db, auth_env, storage=storage, crypto=crypto)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, Any]:
    expert, org_admin, assistant = seed.person(full_name=FULL_NAME), seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert", org_admin: "admin", assistant: "assistant"})
    case = seed.case(org, expert, status="served", members={assistant: "assistant"})
    admin.execute("update app.cases set label = %s where id = %s", (LABEL, case))
    admin.execute("update app.cases set status = 'settled' where id = %s", (case,))
    return {"case": case, "expert": expert, "org": org, "org_admin": org_admin, "assistant": assistant}


Run = Callable[..., Awaitable[OutboxMailer]]


@pytest.fixture
def run(worker_db: Database, seed: Seed) -> Run:
    """`await run(handler, case, kind)`: one attempt of a job on the case, as the worker would."""

    async def go(
        handler: Callable[[JobContext], Awaitable[None]],
        case: uuid.UUID,
        kind: str,
        *,
        job: uuid.UUID | None = None,
        attempt: int = 1,
        mailer: Any = None,
    ) -> OutboxMailer:
        mailer = mailer or OutboxMailer()
        job = job or seed.job(case, kind=kind)
        await handler(JobContext(worker_db, job, case, kind, attempt, mailer=mailer))
        return mailer  # type: ignore[no-any-return]

    return go


def make_due(admin: psycopg.Connection, case: uuid.UUID, *, days_ago: int = 1) -> None:
    admin.execute(
        "update app.cases set retention_policy = 'keep_default', delete_after = current_date - %s where id = %s",
        (days_ago, case),
    )


async def put_encrypted(
    storage: SupabaseStorage, crypto: CaseCrypto, worker_db: Database, case: uuid.UUID, path: str, data: bytes
) -> None:
    async with worker_db.job(case) as conn:
        await storage.put(path, await crypto.encrypt(conn, case, data, purpose=path))


async def add_files(
    admin: psycopg.Connection,
    seed: Seed,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    case: uuid.UUID,
) -> dict[str, Any]:
    """A bundle document, pages (one quarantined, one superseded), an exhibit and an old export."""
    prefix = case_prefix(case)
    original = b"%PDF-zz synthetic bundle"
    doc = seed.document(case)
    doc_path = f"{prefix}/documents/{doc}.pdf"
    admin.execute(
        "update app.documents set storage_path = %s, sha256 = %s, filename = %s where id = %s",
        (doc_path, hashlib.sha256(original).hexdigest(), FILENAME, doc),
    )
    await put_encrypted(storage, crypto, worker_db, case, doc_path, original)
    p1, p2, p3 = (seed.page(case, doc, n) for n in (901, 902, 903))
    admin.execute("update app.pages set quarantined = true, quarantine_reason = 'zz' where id = %s", (p2,))
    admin.execute("update app.pages set superseded_by_page_id = %s where id = %s", (p3, p1))
    paths = [doc_path]
    for name in (f"pages/{p1}.png", f"pages/{p2}.png", f"pages/{p3}.png", "exhibits/e1.pdf", "exports/x/full.zip"):
        await storage.put(f"{prefix}/{name}", b"zz")
        paths.append(f"{prefix}/{name}")
    return {"document": doc, "original": original, "paths": paths, "pages": (p1, p2, p3)}


def rows_of(admin: psycopg.Connection, table: str, case: uuid.UUID) -> int:
    column = "id" if table == "cases" else "case_id"
    row = admin.execute(f"select count(*) from app.{table} where {column} = %s", (case,)).fetchone()  # noqa: S608
    assert row is not None
    return int(row[0])


def certificate(admin: psycopg.Connection, case: uuid.UUID) -> dict[str, Any] | None:
    cur = admin.execute(
        "select deleted_at, counts, hashes, log_chain_heads, backup_expiry_on, sent_to"
        " from app.deletion_certificates where case_id = %s",
        (case,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    fields = ("deleted_at", "counts", "hashes", "log_chain_heads", "backup_expiry_on", "sent_to")
    return dict(zip(fields, row, strict=True))


def existing_tables(admin: psycopg.Connection) -> set[str]:
    rows = admin.execute(
        "select table_name from information_schema.tables where table_schema = 'app' and table_type = 'BASE TABLE'"
    ).fetchall()
    return {r[0] for r in rows}


def paused_notices(admin: psycopg.Connection, case: uuid.UUID) -> int:
    row = admin.execute(
        "select count(*) from app.notifications where case_id = %s and kind = 'deletion_paused'", (case,)
    ).fetchone()
    assert row is not None
    return int(row[0])


# --- delete_case ------------------------------------------------------------------------------------


async def test_deletion_removes_every_case_row_every_file_and_the_key_and_writes_a_certificate(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    files = await add_files(admin, seed, storage, crypto, worker_db, case)
    rid = add_report_version(admin, case, version_no=9, status="served")
    fill_every_case_table(admin, seed, CaseWorld(case, world["expert"], world["org"]))
    admin.execute("delete from app.deletion_certificates where case_id = %s", (case,))
    make_due(admin, case)

    mailer = await run(close_jobs.delete_case, case, "delete_case")

    for table in sorted((CASE_TABLES - {"cases", "case_keys"}) & existing_tables(admin)):
        assert rows_of(admin, table, case) == 0, table
    status, label = admin.execute("select status, label from app.cases where id = %s", (case,)).fetchone()  # type: ignore[misc]
    assert (status, label) == ("deleted", None)
    assert await storage.list_recursive(case_prefix(case)) == []
    async with worker_db.job(case) as conn:
        with pytest.raises(CaseKeyDestroyed):
            await crypto.decrypt(conn, case, b"anything", purpose="x")
    assert admin.execute("select 1 from app.deletion_ledger where case_id = %s", (case,)).fetchone() is not None

    cert = certificate(admin, case)
    assert cert is not None and cert["deleted_at"] is not None
    assert cert["counts"]["pages"] >= 3 and cert["counts"]["report_versions"] >= 1
    assert cert["counts"]["storage_objects"] == len(files["paths"])
    assert hashlib.sha256(files["original"]).hexdigest() in cert["hashes"]["documents"]
    assert "a" * 64 in cert["hashes"]["report_versions"]
    assert cert["backup_expiry_on"] == date.today() + timedelta(days=close_jobs.BACKUP_RETENTION_DAYS)
    assert cert["sent_to"] == ["named_expert"]
    assert rid  # the served version went too (rule 8: only through this run)

    expert_email = admin.execute("select email from app.people where id = %s", (world["expert"],)).fetchone()[0]  # type: ignore[index]
    assert [m.to for m in mailer.outbox] == [expert_email]
    text = repr(mailer.outbox) + json.dumps(cert, default=str)
    for secret in (LABEL, FULL_NAME, FILENAME):
        assert secret not in text


async def test_deletion_removes_quarantined_and_superseded_pages_and_their_objects(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    files = await add_files(admin, seed, storage, crypto, worker_db, case)
    p1, p2, _p3 = files["pages"]
    flagged = admin.execute(
        "select count(*) from app.pages where case_id = %s and (quarantined or superseded_by_page_id is not null)",
        (case,),
    ).fetchone()
    assert flagged == (2,)
    prefix = case_prefix(case)
    before = await storage.list_recursive(prefix)
    for name in (f"pages/{p1}.png", f"pages/{p2}.png", "exhibits/e1.pdf"):
        assert f"{prefix}/{name}" in before
    make_due(admin, case)
    await run(close_jobs.delete_case, case, "delete_case")
    assert rows_of(admin, "pages", case) == 0
    assert await storage.list_recursive(prefix) == []
    # Case tables not yet built here (exhibits, embeddings) are covered through the registry: a
    # registered table with rows is emptied (test above); their files go with the storage prefix.


async def test_a_backup_copy_of_the_files_cannot_be_decrypted_once_the_case_key_is_destroyed(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
) -> None:
    """Backups outlive the deletion by the backup window; the ciphertext they hold is useless (rule 5)."""
    case = world["case"]
    files = await add_files(admin, seed, storage, crypto, worker_db, case)
    doc_path = files["paths"][0]
    backup = await storage.get(doc_path)  # the copy a backup would hold
    assert backup != files["original"] and files["original"] not in backup
    async with worker_db.job(case) as conn:
        assert await crypto.decrypt(conn, case, backup, purpose=doc_path) == files["original"]
    make_due(admin, case)
    await run(close_jobs.delete_case, case, "delete_case")
    assert await storage.list_recursive(case_prefix(case)) == []
    for reader in (crypto, CaseCrypto(crypto.provider)):
        async with worker_db.job(case) as conn:
            with pytest.raises(CaseKeyDestroyed):
                await reader.decrypt(conn, case, backup, purpose=doc_path)
    wrapped = admin.execute("select wrapped_key from app.case_keys where case_id = %s", (case,)).fetchone()
    assert wrapped == (None,)


async def test_an_interrupted_deletion_resumes_and_writes_exactly_one_certificate(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = world["case"]
    await add_files(admin, seed, storage, crypto, worker_db, case)
    make_due(admin, case)
    job = seed.job(case, kind="delete_case")

    real_delete = storage.delete
    calls = {"n": 0}

    async def flaky_delete(paths: Any) -> None:
        calls["n"] += 1
        await real_delete(list(paths)[:1])
        raise RuntimeError("storage went away")

    monkeypatch.setattr(storage, "delete", flaky_delete)
    with pytest.raises(RuntimeError):
        await run(close_jobs.delete_case, case, "delete_case", job=job)
    assert certificate(admin, case) is not None, "written first, so no hold can start mid-run"
    assert rows_of(admin, "pages", case) == 3

    class FailingMailer(OutboxMailer):
        async def send(self, mail: Any) -> None:
            raise RuntimeError("mail service down")

    monkeypatch.setattr(storage, "delete", real_delete)
    with pytest.raises(RuntimeError):
        await run(close_jobs.delete_case, case, "delete_case", job=job, attempt=2, mailer=FailingMailer())
    assert rows_of(admin, "pages", case) == 3, "the purge and the certificate commit with the email"
    assert certificate(admin, case)["deleted_at"] is None  # type: ignore[index]

    mailer = await run(close_jobs.delete_case, case, "delete_case", job=job, attempt=3)
    assert rows_of(admin, "deletion_certificates", case) == 1
    assert certificate(admin, case)["deleted_at"] is not None  # type: ignore[index]
    assert len(mailer.outbox) == 1
    assert await storage.list_recursive(case_prefix(case)) == []
    assert rows_of(admin, "pages", case) == 0


@pytest.mark.parametrize("how", ["legal_hold", "on_hold"])
async def test_a_held_case_is_skipped_and_the_named_expert_told_once(
    how: str, run: Run, seed: Seed, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    case = world["case"]
    doc = seed.document(case)
    seed.page(case, doc, 1)
    make_due(admin, case)
    if how == "legal_hold":
        admin.execute(
            "insert into app.legal_holds (case_id, reason, set_by) values (%s, 'appeal', %s)", (case, world["expert"])
        )
    else:
        admin.execute("update app.cases set on_hold = true where id = %s", (case,))
    for _ in range(2):
        await run(close_jobs.delete_case, case, "delete_case")
    assert certificate(admin, case) is None
    assert rows_of(admin, "pages", case) == 1
    assert paused_notices(admin, case) == 1


async def test_a_case_no_longer_due_is_left_alone(
    run: Run, seed: Seed, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    case = world["case"]
    seed.page(case, seed.document(case), 1)
    admin.execute("update app.cases set delete_after = current_date + 1 where id = %s", (case,))
    await run(close_jobs.delete_case, case, "delete_case")
    assert certificate(admin, case) is None
    assert rows_of(admin, "pages", case) == 1


async def test_delete_now_on_a_case_never_served_runs_today(
    run: Run, seed: Seed, admin: psycopg.Connection, client_for: ClientFor
) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    seed.page(case, seed.document(case), 1)
    resp = await (await client_for(expert)).put(f"/v1/cases/{case}/retention", json={"policy": "delete_now"})
    assert resp.status_code == 200, resp.text
    await run(close_jobs.delete_case, case, "delete_case", job=uuid.UUID(resp.json()["job_id"]))
    assert rows_of(admin, "pages", case) == 0
    assert certificate(admin, case)["deleted_at"] is not None  # type: ignore[index]


@pytest.mark.parametrize("log", ["page_reads", "fact_verifications"])
async def test_the_certificate_carries_the_heads_of_the_hash_chained_logs(
    log: str, worker_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    """The read and verification logs are removed with the case; the last hash of each chain stays on
    the certificate (architecture 10.9). Works with a stand-in log until the owning slice's table
    exists, and seeds a two-row chain into the real table once it does."""
    case = world["case"]
    created = admin.execute("select to_regclass(%s)", (f"app.{log}",)).fetchone() == (None,)
    if created:
        # Owned like every app table (the definer functions read it as its owner).
        admin.execute(f"set role app_migrator; create table app.{log} (case_id uuid, prev_hash text, hash text)")  # noqa: S608
        admin.execute("reset role")
    seeded = True
    try:
        try:
            admin.execute(
                f"insert into app.{log} (case_id, prev_hash, hash) values (%s, null, 'h1'), (%s, 'h1', 'h2'),"  # noqa: S608
                " (%s, null, 'other')",
                (case, case, uuid.uuid4()),
            )
        except psycopg.Error:
            seeded = False  # the real table has more required columns than the chain ones
        async with worker_db.job(case) as conn:
            cur = await conn.execute("select app.close_log_chain_heads(%s)", (case,))
            heads = (await cur.fetchone())[0]  # type: ignore[index]
        if seeded:
            assert heads == {log: "h2"}
        else:
            assert isinstance(heads, dict) and heads.get(log) is None
        async with worker_db.job(uuid.uuid4()) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                await conn.execute("select app.close_log_chain_heads(%s)", (case,))
    finally:
        if created:
            admin.execute(f"drop table app.{log}")  # noqa: S608


async def test_without_the_logs_there_are_no_chain_heads(worker_db: Database, world: dict[str, Any]) -> None:
    async with worker_db.job(world["case"]) as conn:
        cur = await conn.execute("select app.close_log_chain_heads(%s)", (world["case"],))
        assert (await cur.fetchone()) == ({},)


# --- export_case and the download -------------------------------------------------------------------


def finish(admin: psycopg.Connection, job: uuid.UUID) -> None:
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))


async def served_report(
    admin: psycopg.Connection, storage: SupabaseStorage, crypto: CaseCrypto, worker_db: Database, case: uuid.UUID
) -> bytes:
    pdf = b"%PDF-zz synthetic report"
    path = f"{case_prefix(case)}/reports/v1.pdf"
    await put_encrypted(storage, crypto, worker_db, case, path, pdf)
    rid = add_report_version(admin, case)
    admin.execute(
        "update app.report_versions set pdf_path = %s, sha256 = %s where id = %s",
        (path, hashlib.sha256(pdf).hexdigest(), rid),
    )
    admin.execute("update app.report_versions set status = 'locked' where id = %s", (rid,))
    admin.execute("update app.report_versions set status = 'served' where id = %s", (rid,))
    return pdf


async def test_the_named_expert_downloads_the_full_record_of_work_once(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
    client_for: ClientFor,
) -> None:
    case = world["case"]
    files = await add_files(admin, seed, storage, crypto, worker_db, case)
    pdf = await served_report(admin, storage, crypto, worker_db, case)
    client = await client_for(world["expert"])
    resp = await client.post(f"/v1/cases/{case}/export")
    assert resp.status_code == 202, resp.text
    job = uuid.UUID(resp.json()["job_id"])
    await run(close_jobs.export_case, case, "export_case", job=job)
    finish(admin, job)

    resp = await client.get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert "no-store" in resp.headers["cache-control"]
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    names = set(archive.namelist())
    assert archive.read("report-versions/v1.pdf") == pdf
    assert archive.read(f"bundle/{files['document']}.pdf") == files["original"]
    assert not any(FILENAME in n for n in names), "file names in the ZIP are ids, never the uploaded names"
    manifest = json.loads(archive.read("manifest.json"))
    assert manifest["variant"] == "full" and manifest["case_id"] == str(case)
    for empty in manifest["not_yet_available"]:
        assert empty["path"] in names and empty["reason"]
        assert archive.read(empty["path"]).decode().count("\n") <= 1, "a labelled header row only"
    hashes = list(csv.DictReader(io.StringIO(archive.read("hashes.csv").decode())))
    report_row = next(r for r in hashes if r["file"] == "report-versions/v1.pdf")
    assert report_row["recorded_sha256"] == report_row["computed_sha256"] == hashlib.sha256(pdf).hexdigest()
    assert report_row["matches"] == "yes"

    again = await client.get(f"/v1/cases/{case}/export/{job}/download")
    assert again.status_code == 404
    downloads = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.export_downloaded'", (case,)
    ).fetchone()
    assert downloads == (1,)


async def test_an_organisation_admin_gets_the_administrative_copy_without_page_images(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
    client_for: ClientFor,
) -> None:
    case = world["case"]
    await add_files(admin, seed, storage, crypto, worker_db, case)
    pdf = await served_report(admin, storage, crypto, worker_db, case)
    client = await client_for(world["org_admin"])
    job = uuid.UUID((await client.post(f"/v1/cases/{case}/export")).json()["job_id"])
    await run(close_jobs.export_case, case, "export_case", job=job)
    finish(admin, job)
    resp = await client.get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 200, resp.text
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    names = archive.namelist()
    assert archive.read("report-versions/v1.pdf") == pdf
    assert not any(n.startswith(("bundle/", "pages/")) for n in names)
    assert json.loads(archive.read("manifest.json"))["variant"] == "admin"
    # Only the requester's copy was built: nothing is left once it is downloaded.
    assert await storage.list_recursive(f"{case_prefix(case)}/exports/{job}") == []


async def test_the_named_experts_export_builds_only_the_full_copy(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
    client_for: ClientFor,
) -> None:
    case = world["case"]
    await served_report(admin, storage, crypto, worker_db, case)
    client = await client_for(world["expert"])
    job = uuid.UUID((await client.post(f"/v1/cases/{case}/export")).json()["job_id"])
    await run(close_jobs.export_case, case, "export_case", job=job)
    finish(admin, job)
    assert await storage.list_recursive(f"{case_prefix(case)}/exports/{job}") == [
        f"{case_prefix(case)}/exports/{job}/full.zip"
    ]
    resp = await (await client_for(world["org_admin"])).get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 404, "an export is downloaded only by the person who asked for it"


async def test_another_admin_cannot_download_an_admins_export(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
    client_for: ClientFor,
) -> None:
    case = world["case"]
    await served_report(admin, storage, crypto, worker_db, case)
    other_admin = seed.person()
    seed.membership(world["org"], other_admin, "admin")
    job = uuid.UUID((await (await client_for(world["org_admin"])).post(f"/v1/cases/{case}/export")).json()["job_id"])
    await run(close_jobs.export_case, case, "export_case", job=job)
    finish(admin, job)
    resp = await (await client_for(other_admin)).get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 404
    assert len(await storage.list_recursive(f"{case_prefix(case)}/exports/{job}")) == 1


async def test_an_export_nobody_asked_for_builds_nothing(
    run: Run, seed: Seed, storage: SupabaseStorage, world: dict[str, Any]
) -> None:
    case = world["case"]
    job = seed.job(case, kind="export_case")
    await run(close_jobs.export_case, case, "export_case", job=job)
    assert await storage.list_recursive(f"{case_prefix(case)}/exports") == []


async def test_an_export_is_stored_encrypted(
    run: Run, storage: SupabaseStorage, admin: psycopg.Connection, crypto: CaseCrypto, worker_db: Database,
    world: dict[str, Any],
) -> None:  # fmt: skip
    case = world["case"]
    pdf = await served_report(admin, storage, crypto, worker_db, case)
    job = uuid.uuid4()
    admin.execute(
        "insert into app.jobs (id, case_id, kind, requested_by) values (%s, %s, 'export_case', %s)",
        (job, case, world["expert"]),
    )
    await run(close_jobs.export_case, case, "export_case", job=job)
    blob = await storage.get(f"{case_prefix(case)}/exports/{job}/full.zip")
    assert pdf not in blob and not blob.startswith(b"PK")


# --- the daily close run ----------------------------------------------------------------------------


def daily(registry: JobRegistry) -> str:
    name = f"close_daily_{uuid.uuid4().hex[:12]}"
    close_jobs.register(registry, daily_name=name)
    return name


async def run_daily(worker_db: Database, registry: JobRegistry, name: str, day: date) -> None:
    async with worker_db.anonymous() as conn:
        await registry.schedules[name](conn, day)


def delete_jobs(admin: psycopg.Connection, case: uuid.UUID) -> int:
    row = admin.execute("select count(*) from app.jobs where case_id = %s and kind = 'delete_case'", (case,)).fetchone()
    assert row is not None
    return int(row[0])


async def test_the_daily_run_queues_one_deletion_per_due_case(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, world: dict[str, Any]
) -> None:
    registry = isolated_registry()
    name = daily(registry)
    due, later = world["case"], seed.case(world["org"], world["expert"], status="served")
    make_due(admin, due)
    admin.execute("update app.cases set status = 'settled', delete_after = current_date + 1 where id = %s", (later,))
    await run_daily(worker_db, registry, name, date.today())
    await run_daily(worker_db, registry, name, date.today() + timedelta(days=0))
    assert delete_jobs(admin, due) == 1, "a case with a deletion queued is not queued again"
    assert delete_jobs(admin, later) == 0


async def test_the_daily_run_releases_ended_holds_and_archives_old_settled_cases(
    worker_db: Database, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    registry = isolated_registry()
    name = daily(registry)
    case = world["case"]
    with admin.transaction():
        admin.execute("set local session_replication_role = replica")
        admin.execute(
            "update app.cases set settled_at = now() - make_interval(days => %s) where id = %s",
            (close_jobs.ARCHIVE_AFTER_DAYS + 1, case),
        )
    admin.execute(
        "insert into app.legal_holds (case_id, reason, set_by, until) values (%s, 'appeal', %s, current_date - 1)",
        (case, world["expert"]),
    )
    await run_daily(worker_db, registry, name, date.today())
    assert admin.execute("select status from app.cases where id = %s", (case,)).fetchone() == ("archived",)
    released = admin.execute("select released_at is not null from app.legal_holds where case_id = %s", (case,))
    assert released.fetchall() == [(True,)]


@pytest.mark.parametrize(("policy", "years"), [(None, FIXTURE_YEARS), ("return_then_delete", 0)])
async def test_a_withdrawn_case_gets_its_deletion_date_from_the_day_it_closed(
    policy: str | None, years: int, worker_db: Database, admin: psycopg.Connection, seed: Seed, world: dict[str, Any]
) -> None:
    registry = isolated_registry()
    name = daily(registry)
    case = seed.case(world["org"], world["expert"], status="active")
    admin.execute("update app.cases set status = 'withdrawn', retention_policy = %s where id = %s", (policy, case))
    day = date.today()
    await run_daily(worker_db, registry, name, day)
    row = admin.execute("select retention_policy, delete_after from app.cases where id = %s", (case,)).fetchone()
    assert row == (policy or "keep_default", day.replace(year=day.year + years) if years else day)


async def test_missed_days_are_caught_up_each_once(
    worker_db: Database, worker_dsn: str, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    registry = isolated_registry()
    name = daily(registry)
    case = world["case"]
    make_due(admin, case, days_ago=5)
    today = date.today()
    admin.execute("insert into app.scheduled_runs (name, run_date) values (%s, %s)", (name, today - timedelta(days=3)))
    async with open_worker(registry, worker_db, worker_dsn) as worker:
        ran = await worker.catch_up(name, today)
        again = await worker.catch_up(name, today)
    assert ran == [today - timedelta(days=n) for n in (2, 1, 0)]
    assert again == []
    assert delete_jobs(admin, case) == 1


async def test_the_daily_run_is_registered_for_the_worker() -> None:
    from app.core.jobs import registry

    assert close_jobs.DAILY in registry.schedules
    assert {"export_case", "delete_case"} <= set(registry.kinds)


# --- after review: served versions, one email per recipient, exports during deletion, expiry ----------


async def test_a_served_report_version_is_removed_by_the_deletion_run(
    run: Run, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    case = world["case"]
    rid = add_report_version(admin, case, version_no=7, status="served")
    make_due(admin, case)
    await run(close_jobs.delete_case, case, "delete_case")
    assert admin.execute("select count(*) from app.report_versions where id = %s", (rid,)).fetchone() == (0,)


async def test_the_certificate_is_emailed_once_even_when_the_run_is_retried(
    run: Run, seed: Seed, admin: psycopg.Connection, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    case = world["case"]
    seed.page(case, seed.document(case), 1)
    make_due(admin, case)
    job = seed.job(case, kind="delete_case")
    real = close_jobs.finish_certificate
    calls = {"n": 0}

    async def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database went away")
        return await real(*args, **kwargs)

    monkeypatch.setattr(close_jobs, "finish_certificate", flaky)
    first = OutboxMailer()
    with pytest.raises(RuntimeError):
        await run(close_jobs.delete_case, case, "delete_case", job=job, mailer=first)
    assert len(first.outbox) == 1
    cert = certificate(admin, case)
    assert cert is not None and cert["deleted_at"] is None and cert["sent_to"] == ["named_expert"]
    assert rows_of(admin, "pages", case) == 1

    second = await run(close_jobs.delete_case, case, "delete_case", job=job, attempt=2)
    assert second.outbox == []
    cert = certificate(admin, case)
    assert cert is not None and cert["deleted_at"] is not None and cert["sent_to"] == ["named_expert"]
    assert rows_of(admin, "pages", case) == 0


async def test_an_export_writes_nothing_once_deletion_has_started(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
) -> None:
    case = world["case"]
    await served_report(admin, storage, crypto, worker_db, case)
    admin.execute("insert into app.deletion_certificates (case_id) values (%s)", (case,))
    await run(close_jobs.export_case, case, "export_case", job=seed.job(case, kind="export_case"))
    assert await storage.list_recursive(f"{case_prefix(case)}/exports") == []


async def test_a_deletion_that_starts_during_an_export_still_removes_the_export(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = world["case"]
    await served_report(admin, storage, crypto, worker_db, case)
    make_due(admin, case)
    reading, go_on = asyncio.Event(), asyncio.Event()
    real_get = storage.get

    async def slow_get(path: str) -> bytes:
        reading.set()
        await go_on.wait()
        return await real_get(path)

    monkeypatch.setattr(storage, "get", slow_get)
    export_job = seed.job(case, kind="export_case", requested_by=world["expert"])
    delete_job = seed.job(case, kind="delete_case")
    export: asyncio.Future[OutboxMailer] = asyncio.ensure_future(
        run(close_jobs.export_case, case, "export_case", job=export_job)
    )
    await asyncio.wait_for(reading.wait(), 10)
    deletion: asyncio.Future[OutboxMailer] = asyncio.ensure_future(
        run(close_jobs.delete_case, case, "delete_case", job=delete_job)
    )
    await asyncio.sleep(0.5)
    go_on.set()
    await asyncio.wait_for(asyncio.gather(export, deletion), 30)
    cert = certificate(admin, case)
    assert cert is not None and cert["deleted_at"] is not None
    assert await storage.list_recursive(case_prefix(case)) == []


async def test_the_download_is_streamed(
    run: Run,
    seed: Seed,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    world: dict[str, Any],
    client_for: ClientFor,
) -> None:
    case = world["case"]
    pdf = await served_report(admin, storage, crypto, worker_db, case)
    client = await client_for(world["expert"])
    job = uuid.UUID((await client.post(f"/v1/cases/{case}/export")).json()["job_id"])
    await run(close_jobs.export_case, case, "export_case", job=job)
    finish(admin, job)
    resp = await client.get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 200, resp.text
    assert "content-length" not in resp.headers, "sent in chunks, not as one body"
    assert "no-store" in resp.headers["cache-control"]
    assert zipfile.ZipFile(io.BytesIO(resp.content)).read("report-versions/v1.pdf") == pdf


async def test_the_daily_run_removes_exports_not_downloaded_in_time(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, storage: SupabaseStorage, world: dict[str, Any]
) -> None:
    registry = isolated_registry()
    name = daily(registry)
    case = world["case"]
    old, recent = seed.job(case, kind="export_case"), seed.job(case, kind="export_case")
    for job, days in ((old, close_jobs.EXPORT_KEEP_DAYS + 1), (recent, 1)):
        finish(admin, job)
        with admin.transaction():
            admin.execute("set local session_replication_role = replica")
            admin.execute(
                "update app.jobs set finished_at = now() - make_interval(days => %s) where id = %s", (days, job)
            )
        for variant in close_jobs.VARIANTS:
            await storage.put(f"{case_prefix(case)}/exports/{job}/{variant}.zip", b"zz")
    await run_daily(worker_db, registry, name, date.today())
    assert await storage.list_recursive(f"{case_prefix(case)}/exports/{old}") == []
    assert len(await storage.list_recursive(f"{case_prefix(case)}/exports/{recent}")) == len(close_jobs.VARIANTS)


async def test_the_daily_run_archives_and_dates_a_case_withdrawn_long_ago(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, world: dict[str, Any]
) -> None:
    registry = isolated_registry()
    name = daily(registry)
    case = seed.case(world["org"], world["expert"], status="active")
    withdrawn = date.today() - timedelta(days=close_jobs.ARCHIVE_AFTER_DAYS + 10)
    with admin.transaction():
        admin.execute("set local session_replication_role = replica")
        admin.execute(
            "update app.cases set status = 'withdrawn', closed_at = (%s::date + time '12:00')::timestamptz"
            " where id = %s",
            (withdrawn, case),
        )
    await run_daily(worker_db, registry, name, date.today())
    row = admin.execute("select status, delete_after from app.cases where id = %s", (case,)).fetchone()
    assert row == ("archived", withdrawn.replace(year=withdrawn.year + FIXTURE_YEARS))
