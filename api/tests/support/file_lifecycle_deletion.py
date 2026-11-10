"""Helpers for the deletion audit tests (AUDIT-12 D1-D6): a case with a copy of everything, and a sweep for it.

Everything is made up. Each world carries a distinctive marker (`AUDIT12-MARKER-<uuid>`) in the plaintext
columns the product writes for a file (upload and document file names, the case label) and in one plaintext
column of every case table that accepts it, so a survivor is found by its marker and not by guesswork.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import boto3
import httpx
import psycopg
from botocore.config import Config

from app.core.contracts.case_tables import BUNDLE_SCOPED_TABLES, CASE_TABLES, KEPT_AFTER_CASE_DELETION
from app.core.crypto import CaseCrypto
from app.core.db import Database
from app.core.storage import CaseFileStorage, case_prefix
from app.features.pipeline import paths
from tests.support.case_rows import CaseWorld, fill_every_case_table
from tests.support.files import STORAGE_BUCKET, STORAGE_REGION, SyntheticFile, storage_env
from tests.support.seed import Seed
from tests.support.uploads import complete_and_finalize, put, put_all, start

SWEPT_SCHEMAS = ("app", "procrastinate")

# What may still hold the case id after deletion, by design (state-machines.md "Deletion", `case_tables.py`):
# (`bundles`: id, source_case_id, page_count and a date; it outlives the case, ADR 0034)
# the tombstone, the key row (wrapped key nulled), the append-only log, the ledger, the certificate, and the
# tables a case's deletion never removes (support grants, custody transfers, replacement links).
BY_DESIGN = frozenset({"cases", "case_keys", "audit_log", "deletion_ledger", "deletion_certificates", "bundles"}) | (
    KEPT_AFTER_CASE_DELETION
)

# Columns never overwritten with a marker: they locate files, are checked values, or are keys.
_SKIP_COLUMN = re.compile(r"(_path|path|sha256|hash|status|kind|code|key|email|step|role|type|state|action|target)$")


def picture(marker: str) -> bytes:
    """A real 8x8 picture (the image routes open it) with the marker in its text chunk."""
    import io

    from PIL import Image, PngImagePlugin

    info = PngImagePlugin.PngInfo()
    info.add_text("note", marker)
    out = io.BytesIO()
    Image.new("RGB", (8, 8), (250, 250, 250)).save(out, format="PNG", pnginfo=info)
    return out.getvalue()


def new_marker() -> str:
    return f"AUDIT12-MARKER-{uuid.uuid4()}"


def tiny_file(marker: str, seed: int) -> SyntheticFile:
    """One part of 300 made-up bytes (the part is sealed and stored, so it is a real object)."""
    return SyntheticFile(300, seed=seed)


def bucket_keys(contains: str) -> list[str]:
    """Every object key in the whole (shared) bucket that contains `contains`, whatever its prefix."""
    env = storage_env()
    client = boto3.client(
        "s3",
        endpoint_url=env["S3_ENDPOINT_URL"],
        region_name=STORAGE_REGION,
        aws_access_key_id=env["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
        config=Config(retries={"max_attempts": 2}),
    )
    found: list[str] = []
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=STORAGE_BUCKET):
        found += [o["Key"] for o in page.get("Contents", []) if contains in o["Key"]]
    return sorted(found)


def sweep(admin: psycopg.Connection, case: uuid.UUID, marker: str) -> dict[str, tuple[int, int]]:
    """For every table of schemas app and procrastinate: (rows naming the case id, rows holding the marker).

    A row is read as its text form, so any column counts (ids inside jsonb arguments, the marker as text or as
    the hex a `bytea` prints). Only tables with a hit are returned.
    """
    tables = admin.execute(
        "select table_schema, table_name from information_schema.tables"
        " where table_schema = any(%s) and table_type = 'BASE TABLE' order by 1, 2",
        (list(SWEPT_SCHEMAS),),
    ).fetchall()
    hits: dict[str, tuple[int, int]] = {}
    for schema, name in tables:
        row = admin.execute(
            f"select count(*) filter (where t::text like %s), count(*) filter (where t::text like %s"  # noqa: S608
            f' or t::text like %s) from {schema}."{name}" t',
            (f"%{case}%", f"%{marker}%", f"%{marker.encode().hex()}%"),
        ).fetchone()
        assert row is not None
        if row[0] or row[1]:
            hits[name] = (int(row[0]), int(row[1]))
    return hits


def case_id_tables(hits: dict[str, tuple[int, int]]) -> set[str]:
    return {t for t, (ids, _) in hits.items() if ids}


def marker_tables(hits: dict[str, tuple[int, int]]) -> set[str]:
    return {t for t, (_, marked) in hits.items() if marked}


def plant_markers(admin: psycopg.Connection, case: uuid.UUID, marker: str) -> dict[str, list[str]]:
    """Write the marker into one plaintext column (one row) of each case table that accepts it.

    Columns a table refuses (checks, foreign keys, append-only triggers) are skipped; what was planted is returned,
    so a test can show the sweep was not vacuous.
    """
    existing = {
        r[0]
        for r in admin.execute(
            "select table_name from information_schema.tables where table_schema = 'app' and table_type = 'BASE TABLE'"
        ).fetchall()
    }
    planted: dict[str, list[str]] = {}
    for table in sorted((CASE_TABLES | KEPT_AFTER_CASE_DELETION) & existing):
        if table in {"case_keys", "deletion_ledger", "deletion_certificates", "audit_log", "cases"}:
            # `cases`: its text columns are pack ids and the label, which the seed already marks
            continue
        if table == "cases":
            where = "id = %s"
        elif table in BUNDLE_SCOPED_TABLES:
            where = "bundle_id = (select bundle_id from app.cases where id = %s)"
        else:
            where = "case_id = %s"
        columns = admin.execute(
            "select column_name, data_type from information_schema.columns where table_schema = 'app'"
            " and table_name = %s and data_type in ('text', 'character varying', 'jsonb') order by ordinal_position",
            (table,),
        ).fetchall()
        for name, data_type in columns:
            if _SKIP_COLUMN.search(name):
                continue
            value = "to_jsonb(%s::text)" if data_type == "jsonb" else "%s"
            try:
                cur = admin.execute(
                    f"update app.{table} set {name} = {value} where ctid in"  # noqa: S608
                    f" (select ctid from app.{table} where {where} limit 1)",
                    (marker, case),
                )
            except psycopg.Error:
                continue
            if cur.rowcount:
                planted.setdefault(table, []).append(name)
                break  # one column per table is enough to see whether the table's rows survive
    return planted


@dataclass
class Lifecycle:
    """A due-for-deletion case holding every kind of stored copy the product makes."""

    case: uuid.UUID
    org: uuid.UUID
    expert: uuid.UUID
    assistant: uuid.UUID
    viewer: uuid.UUID
    org_admin: uuid.UUID
    outsider: uuid.UUID
    marker: str
    document: str = ""
    original_sha: str = ""
    original: bytes = b""
    page_no: int = 777  # past the case-row seeders' page numbers
    page_id: uuid.UUID | None = None
    done_upload: str = ""
    open_upload: str = ""
    cancelled_upload: str = ""
    export_job: uuid.UUID | None = None
    objects: dict[str, str] = field(default_factory=dict)  # what each stored copy is, by its path
    planted: dict[str, list[str]] = field(default_factory=dict)

    @property
    def prefix(self) -> str:
        return case_prefix(self.case)

    def people(self) -> dict[str, uuid.UUID]:
        return {
            "named_expert": self.expert,
            "assistant": self.assistant,
            "viewer": self.viewer,
            "org_admin": self.org_admin,
            "outsider": self.outsider,
        }


def seed_people(seed: Seed, marker: str, *, status: str = "settled") -> Lifecycle:
    expert, org_admin, assistant, viewer, outsider = (seed.person() for _ in range(5))
    org = seed.org(kind="team", members={expert: "expert", org_admin: "admin", assistant: "assistant"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(
        org,
        expert,
        status="active" if status == "active" else "served",
        members={assistant: "assistant", viewer: "viewer"},
    )
    seed.admin.execute("update app.cases set label = %s where id = %s", (marker, case))
    if status != "active":
        seed.admin.execute("update app.cases set status = 'settled' where id = %s", (case,))
    return Lifecycle(case, org, expert, assistant, viewer, org_admin, outsider, marker)


def make_due(admin: psycopg.Connection, case: uuid.UUID) -> None:
    admin.execute(
        "update app.cases set retention_policy = 'keep_default', delete_after = current_date - 1 where id = %s", (case,)
    )


async def put_sealed(
    storage: CaseFileStorage, crypto: CaseCrypto, worker_db: Database, case: uuid.UUID, path: str, data: bytes
) -> None:
    async with worker_db.job(case) as conn:
        await storage.put(path, await crypto.encrypt(conn, case, data, purpose=path))


async def add_stored_copies(
    life: Lifecycle,
    *,
    seed: Seed,
    admin: psycopg.Connection,
    client: httpx.AsyncClient,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    worker_db: Database,
) -> None:
    """A finalised upload with its document and one page (image, thumbnail, one-page PDF), a report file, an open
    upload and a cancelled upload with parts the daily sweep has not reached. All names carry the marker."""
    case, marker = life.case, life.marker
    done = tiny_file(marker, 41)
    life.original, life.original_sha = done.chunk(1), done.sha256()
    life.done_upload = await start(client, case, done, filename=f"{marker}-done.pdf")
    await put_all(client, life.done_upload, done)
    life.document = await complete_and_finalize(client, life.done_upload, done.sha256())
    admin.execute(  # the scan has run on it, as before anything reads a real one (the export needs it)
        "insert into app.document_scans (document_id, bundle_id, clean, media_type)"
        " values (%s, (select bundle_id from app.cases where id = %s), true, 'application/pdf')",
        (life.document, case),
    )
    life.page_id = seed.page(case, uuid.UUID(life.document), life.page_no)
    image = paths.page_image_path(case, life.page_id)
    admin.execute("update app.pages set image_path = %s where id = %s", (image, life.page_id))
    life.objects[image] = "page image"
    await put_sealed(storage, crypto, worker_db, case, image, picture(marker))
    thumb = paths.thumbnail_path(case, life.page_id)
    life.objects[thumb] = "thumbnail"
    await put_sealed(storage, crypto, worker_db, case, thumb, picture(marker))
    for path, label in (
        (paths.page_pdf_path(case, life.page_id), "page pdf"),
        (f"{case_prefix(case)}/reports/{uuid.uuid4()}/report.docx", "report"),
    ):
        life.objects[path] = label
        await put_sealed(storage, crypto, worker_db, case, path, f"{marker}-{label}".encode())
    other = tiny_file(marker, 42)
    life.open_upload = await start(client, case, other, filename=f"{marker}-open.pdf")
    assert (await put(client, life.open_upload, 1, other.chunk(1))).status_code == 200
    gone = tiny_file(marker, 43)
    life.cancelled_upload = await start(client, case, gone, filename=f"{marker}-cancelled.pdf")
    assert (await put(client, life.cancelled_upload, 1, gone.chunk(1))).status_code == 200
    cancelled = await client.post(f"/v1/uploads/{life.cancelled_upload}/cancel")
    assert cancelled.status_code == 200, cancelled.text


def fill_and_plant(admin: psycopg.Connection, seed: Seed, life: Lifecycle) -> None:
    """Every case table gets rows (the guard's seeders), then each takes the marker in one plaintext column."""
    fill_every_case_table(admin, seed, CaseWorld(life.case, life.expert, life.org))
    admin.execute("delete from app.deletion_certificates where case_id = %s", (life.case,))
    life.planted = plant_markers(admin, life.case, life.marker)
    make_due(admin, life.case)


def objects_under(storage_objects: list[str], case: uuid.UUID) -> list[str]:
    return [p for p in storage_objects if p.startswith(case_prefix(case) + "/")]


def outside_prefix(keys: list[str], case: uuid.UUID) -> list[str]:
    return [k for k in keys if not k.startswith(case_prefix(case) + "/")]


def status_of(admin: psycopg.Connection, case: uuid.UUID) -> Any:
    row = admin.execute("select status from app.cases where id = %s", (case,)).fetchone()
    assert row is not None
    return row[0]
