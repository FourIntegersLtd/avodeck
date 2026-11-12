"""Security audit 16 (document provenance and output integrity), slice H's part (PV-17a to PV-17d).

Each test asserts the safe outcome and names its finding:

- PV-17a: `hashes.csv` shows a genuine signed report file as matching. The recorded hash of each file is the
  per-format hash frozen at signing (`report_snapshots`), not the version's combined hash; the manifest carries the
  combined hash too. A file that differs from its snapshot still shows `no`.
- PV-17b: the finished archive's SHA-256 is recorded when it is built and checked when it is downloaded (409
  `hash_mismatch` when the stored copy was replaced or no hash was recorded); a good download carries
  `X-Content-SHA256`.
- PV-17c: `manifest.json` says how the record was made: each report file's version number and status (a draft or
  withdrawn version is marked), the case's pack versions, the export job's code version, and each source
  document's recorded SHA-256 and page count.
- PV-17d is deliberately not built (see docs/slices/H-close.md, "As built": the export holds the files the brief
  names; the facts, citations and the checking log are not among them).
"""

# ruff: noqa: F811 - pytest fixtures imported from test_close_jobs are used as arguments
from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import uuid
import zipfile
from typing import Any

import psycopg
import pytest

from app.core.crypto import CaseCrypto
from app.core.db import Database
from app.core.storage import CaseFileStorage, case_prefix
from app.features.close import jobs as close_jobs
from app.features.close import sealed as close_sealed
from app.features.report.signing import version_hash
from tests.repository.test_close_jobs import (  # noqa: F401
    Run,
    add_files,
    app,
    crypto,
    finish,
    put_encrypted,
    run,
    services,
    storage,
    worker_db,
    world,
)
from tests.support.contracts import add_report_version
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def signed_main_report(
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
    case: uuid.UUID,
    *,
    version_no: int = 1,
    status: str = "served",
) -> dict[str, Any]:
    """A main report as real signing leaves it: both files stored, one snapshot per format, and the version's
    `sha256` the combined hash of the two (`version_hash`), not either file's own hash."""
    docx, pdf = f"PK-zz synthetic docx {version_no}".encode(), f"%PDF-zz synthetic report {version_no}".encode()
    docx_path = f"{case_prefix(case)}/reports/v{version_no}.docx"
    pdf_path = f"{case_prefix(case)}/reports/v{version_no}.pdf"
    await put_encrypted(storage, crypto, worker_db, case, docx_path, docx)
    await put_encrypted(storage, crypto, worker_db, case, pdf_path, pdf)
    rid = add_report_version(admin, case, version_no=version_no)
    admin.execute(
        "update app.report_versions set docx_path = %s, pdf_path = %s where id = %s", (docx_path, pdf_path, rid)
    )
    for kind, data in (("docx", docx), ("pdf", pdf)):
        admin.execute(
            "insert into app.report_snapshots (case_id, report_version_id, kind, sha256) values (%s, %s, %s, %s)",
            (case, rid, kind, sha(data)),
        )
    combined = version_hash(sha(docx), sha(pdf))
    if status in ("locked", "served", "withdrawn"):
        admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", (combined, rid))
    if status in ("served", "withdrawn"):
        admin.execute("update app.report_versions set status = 'served' where id = %s", (rid,))
    if status == "withdrawn":
        admin.execute(
            "update app.report_versions set status = 'withdrawn', withdrawn_reason = 'zz_reason' where id = %s", (rid,)
        )
    return {"id": rid, "docx": docx, "pdf": pdf, "combined": combined, "docx_path": docx_path, "pdf_path": pdf_path}


async def build_export(
    run: Run, admin: psycopg.Connection, client: Any, case: uuid.UUID, *, download: bool = True
) -> tuple[uuid.UUID, Any]:
    resp = await client.post(f"/v1/cases/{case}/export")
    job = uuid.UUID(resp.json()["job_id"])
    await run(close_jobs.export_case, case, "export_case", job=job)
    finish(admin, job)
    return job, (await client.get(f"/v1/cases/{case}/export/{job}/download") if download else None)


def table(archive: zipfile.ZipFile, name: str) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(archive.read(name).decode())))


# --- PV-17a -----------------------------------------------------------------------------------------------


async def test_pv17a_a_signed_main_reports_files_each_match_their_own_snapshot_hash(
    run: Run, admin: psycopg.Connection, storage: CaseFileStorage, crypto: CaseCrypto, worker_db: Database,
    world: dict[str, Any], client_for: ClientFor,
) -> None:  # fmt: skip
    case = world["case"]
    report = await signed_main_report(admin, storage, crypto, worker_db, case)
    _, resp = await build_export(run, admin, await client_for(world["expert"]), case)
    archive = zipfile.ZipFile(io.BytesIO(resp.content))
    rows = {r["file"]: r for r in table(archive, "hashes.csv")}
    for name, data in (("report-versions/v1.pdf", report["pdf"]), ("report-versions/v1.docx", report["docx"])):
        assert rows[name]["recorded_sha256"] == rows[name]["computed_sha256"] == sha(data), name
        assert rows[name]["matches"] == "yes", name
    manifest = json.loads(archive.read("manifest.json"))
    assert [r["version_sha256"] for r in manifest["reports"]] == [report["combined"]], "the combined hash is shown too"


async def test_pv17a_an_after_serving_report_whose_version_hash_is_its_docx_hash_matches(
    run: Run, admin: psycopg.Connection, storage: CaseFileStorage, crypto: CaseCrypto, worker_db: Database,
    world: dict[str, Any], client_for: ClientFor,
) -> None:  # fmt: skip
    """Slice G's `lock_version`: `report_versions.sha256` is the docx's own hash; one snapshot, kind `docx`."""
    case = world["case"]
    docx = b"PK-zz synthetic supplementary"
    path = f"{case_prefix(case)}/reports/v2.docx"
    await put_encrypted(storage, crypto, worker_db, case, path, docx)
    rid = add_report_version(admin, case, version_no=2)
    admin.execute("update app.report_versions set type = 'supplementary', docx_path = %s where id = %s", (path, rid))
    admin.execute(
        "insert into app.report_snapshots (case_id, report_version_id, kind, sha256) values (%s, %s, 'docx', %s)",
        (case, rid, sha(docx)),
    )
    admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", (sha(docx), rid))
    _, resp = await build_export(run, admin, await client_for(world["expert"]), case)
    row = next(
        r for r in table(zipfile.ZipFile(io.BytesIO(resp.content)), "hashes.csv") if r["file"].endswith("v2.docx")
    )
    assert row["recorded_sha256"] == sha(docx) and row["matches"] == "yes"


async def test_pv17a_a_file_that_differs_from_its_snapshot_still_shows_no(
    run: Run, admin: psycopg.Connection, storage: CaseFileStorage, crypto: CaseCrypto, worker_db: Database,
    world: dict[str, Any], client_for: ClientFor,
) -> None:  # fmt: skip
    case = world["case"]
    report = await signed_main_report(admin, storage, crypto, worker_db, case)
    await put_encrypted(storage, crypto, worker_db, case, report["pdf_path"], b"%PDF-zz a different file")
    _, resp = await build_export(run, admin, await client_for(world["expert"]), case)
    rows = {r["file"]: r for r in table(zipfile.ZipFile(io.BytesIO(resp.content)), "hashes.csv")}
    assert rows["report-versions/v1.pdf"]["matches"] == "no"
    assert rows["report-versions/v1.docx"]["matches"] == "yes"


# --- PV-17b -----------------------------------------------------------------------------------------------


async def test_pv17b_a_good_download_carries_the_archives_hash(
    run: Run, admin: psycopg.Connection, storage: CaseFileStorage, crypto: CaseCrypto, worker_db: Database,
    world: dict[str, Any], client_for: ClientFor,
) -> None:  # fmt: skip
    case = world["case"]
    await signed_main_report(admin, storage, crypto, worker_db, case)
    job, resp = await build_export(run, admin, await client_for(world["expert"]), case)
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-content-sha256"] == sha(resp.content)
    recorded = admin.execute(
        "select target from app.audit_log where case_id = %s and action = 'case.export_built'", (case,)
    ).fetchall()
    assert recorded == [(f"export:{job}:full:{sha(resp.content)}",)], "the hash is recorded when the archive is built"


async def test_pv17b_a_replaced_stored_archive_is_refused_at_download(
    run: Run, admin: psycopg.Connection, storage: CaseFileStorage, crypto: CaseCrypto, worker_db: Database,
    world: dict[str, Any], client_for: ClientFor,
) -> None:  # fmt: skip
    case = world["case"]
    await signed_main_report(admin, storage, crypto, worker_db, case)
    client = await client_for(world["expert"])
    job, _ = await build_export(run, admin, client, case, download=False)
    other = io.BytesIO()
    with zipfile.ZipFile(other, "w") as forged:
        forged.writestr("report-versions/v1.pdf", b"%PDF-zz forged")
    other.seek(0)
    target = f"{case_prefix(case)}/exports/{job}/full.zip"
    with tempfile.TemporaryFile() as sealed_file:  # validly sealed for this path, different bytes
        async with worker_db.job(case) as conn:
            await close_sealed.seal_file(conn, crypto, case, other, sealed_file, purpose=target)
        sealed_file.seek(0)
        await storage.put(target, sealed_file.read())
    resp = await client.get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "hash_mismatch", resp.text
    assert b"forged" not in resp.content


async def test_pv17b_an_archive_with_no_recorded_hash_is_refused(
    run: Run, admin: psycopg.Connection, storage: CaseFileStorage, crypto: CaseCrypto, worker_db: Database,
    world: dict[str, Any], client_for: ClientFor,
) -> None:  # fmt: skip
    case = world["case"]
    await signed_main_report(admin, storage, crypto, worker_db, case)
    client = await client_for(world["expert"])
    job, _ = await build_export(run, admin, client, case, download=False)
    admin.execute("alter table app.audit_log disable trigger user")
    try:
        admin.execute("delete from app.audit_log where case_id = %s and action = 'case.export_built'", (case,))
    finally:
        admin.execute("alter table app.audit_log enable trigger user")
    resp = await client.get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "hash_mismatch", resp.text


# --- PV-17c -----------------------------------------------------------------------------------------------


async def test_pv17c_the_manifest_says_how_the_record_was_made(
    run: Run, seed: Seed, admin: psycopg.Connection, storage: CaseFileStorage, crypto: CaseCrypto,
    worker_db: Database, world: dict[str, Any], client_for: ClientFor,
) -> None:  # fmt: skip
    case = world["case"]
    files = await add_files(admin, seed, storage, crypto, worker_db, case)
    admin.execute("update app.documents set pages = 12 where id = %s", (files["document"],))
    served = await signed_main_report(admin, storage, crypto, worker_db, case, version_no=1)
    await signed_main_report(admin, storage, crypto, worker_db, case, version_no=2, status="withdrawn")
    await signed_main_report(admin, storage, crypto, worker_db, case, version_no=3, status="draft")
    admin.execute("update app.cases set pack_versions = %s where id = %s", ('{"zz_pack": "1.2.3"}', case))
    client = await client_for(world["expert"])
    resp = await client.post(f"/v1/cases/{case}/export")
    job = uuid.UUID(resp.json()["job_id"])
    admin.execute("update app.jobs set code_version = 'zz-build-1' where id = %s", (job,))
    await run(close_jobs.export_case, case, "export_case", job=job)
    finish(admin, job)
    archive = zipfile.ZipFile(io.BytesIO((await client.get(f"/v1/cases/{case}/export/{job}/download")).content))
    manifest = json.loads(archive.read("manifest.json"))

    assert manifest["pack_versions"] == {"zz_pack": "1.2.3"}
    assert manifest["code_version"] == "zz-build-1"
    by_no = {r["version_no"]: r for r in manifest["reports"]}
    assert {n: r["status"] for n, r in by_no.items()} == {1: "served", 2: "withdrawn", 3: "draft"}
    assert by_no[1]["marking"] is None and by_no[1]["version_sha256"] == served["combined"]
    assert by_no[2]["marking"] == "withdrawn" and by_no[3]["marking"] == "draft"
    assert {f["path"] for f in by_no[3]["files"]} == {"report-versions/v3.pdf", "report-versions/v3.docx"}
    assert manifest["documents"] == [
        {"path": f"bundle/{files['document']}.pdf", "document_id": str(files["document"]),
         "sha256": sha(files["original"]), "pages": 12}
    ]  # fmt: skip
    for name in ("hashes.csv", "manifest.json"):
        assert name in archive.namelist()


async def test_pv17c_the_administrative_copy_lists_no_bundle_documents(
    run: Run, seed: Seed, admin: psycopg.Connection, storage: CaseFileStorage, crypto: CaseCrypto,
    worker_db: Database, world: dict[str, Any], client_for: ClientFor,
) -> None:  # fmt: skip
    case = world["case"]
    await add_files(admin, seed, storage, crypto, worker_db, case)
    await signed_main_report(admin, storage, crypto, worker_db, case)
    _, resp = await build_export(run, admin, await client_for(world["org_admin"]), case)
    manifest = json.loads(zipfile.ZipFile(io.BytesIO(resp.content)).read("manifest.json"))
    assert manifest["variant"] == "admin" and manifest["documents"] == []
    assert manifest["reports"][0]["status"] == "served"
