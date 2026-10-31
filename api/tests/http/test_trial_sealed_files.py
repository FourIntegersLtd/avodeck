"""Slice I's stored files are sealed under the case key (architecture 9; item 3 of the encryption plan).

Every write site stores ciphertext that decrypts (purpose: its storage path) to the original bytes, every
read opens it, and the SHA-256 people check outside the app is still over the plain file. Files written
before sealing (plain PDFs) are converted once by `reseal_plain_files`, which a second run leaves alone.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable

import psycopg
import pytest

from app.core.crypto import CaseKeyDestroyed
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import SupabaseStorage
from app.features.trial.common import trial_path
from app.features.trial.reseal import reseal_plain_files
from app.features.trial.services import current
from app.features.trial.storage import get_sealed
from tests.fixtures.trial.fixtures import (  # noqa: F401
    Scripted,
    aconn,
    app,
    changes,
    combo,
    model,
    reads,
    storage,
    upload_bundle,
    worker_db,
    world,
)
from tests.fixtures.trial.world import CRYPTO, World, agenda_pdf, pdf_of, trial_bundle
from tests.http.test_trial_meetings import AGENDA, agenda, create
from tests.http.test_trial_pack import built, download
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


async def opened(
    storage: SupabaseStorage, aconn: psycopg.AsyncConnection, w: World, path: str, original: bytes | None = None
) -> bytes:
    """The stored object must not be a PDF; opened with the case key (purpose: its path) it is the original."""
    blob = await storage.get(path)
    assert not blob.startswith(b"%PDF"), "stored sealed, never as a plain PDF"
    plain = await CRYPTO.decrypt(aconn, w.case, blob, purpose=path)
    assert plain.startswith(b"%PDF")
    if original is not None:
        assert plain == original
    return plain


def one(admin: psycopg.Connection, query: str, *params: object) -> str:
    row = admin.execute(query, params).fetchone()
    assert row is not None and row[0] is not None
    return str(row[0])


async def test_the_trial_pack_is_stored_sealed_and_downloads_as_the_same_pdf(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    await built(client_for, worker_db, world)
    path = one(admin, "select storage_path from app.trial_packs where case_id = %s", world.case)
    plain = await opened(storage, aconn, world, path)
    served = await download(client_for, world)
    assert served == plain and served.startswith(b"%PDF")
    assert one(admin, "select sha256 from app.trial_packs where case_id = %s", world.case) == sha(plain)


async def test_the_agenda_is_stored_sealed_and_still_read(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
    model: Callable[..., Scripted],
) -> None:
    model()
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    path = one(admin, "select storage_path from app.agenda_versions where meeting_id = %s", created["id"])
    original = agenda_pdf(AGENDA)
    await opened(storage, aconn, world, path, original)
    assert one(admin, "select sha256 from app.agenda_versions where meeting_id = %s", created["id"]) == sha(original)
    assert one(admin, "select count(*) from app.agenda_items where meeting_id = %s", created["id"]) == str(len(AGENDA))


async def test_the_prepared_statement_is_stored_sealed_and_sent_with_its_plain_hash(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
    model: Callable[..., Scripted],
) -> None:
    model()
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    client = await client_for(world.expert)
    url = world.url(f"expert-meetings/{created['id']}")
    prepared = await client.post(f"{url}/prepare")
    assert prepared.status_code == 200, prepared.text
    path = one(admin, "select prepared_path from app.expert_meetings where id = %s", created["id"])
    plain = await opened(storage, aconn, world, path)
    assert prepared.json()["sha256"] == sha(plain)
    sent = await client.post(f"{url}/send-for-signature")
    assert sent.status_code == 200, sent.text
    assert sent.content == plain and sent.content.startswith(b"%PDF")
    assert sent.headers["x-content-sha256"] == sha(sent.content)


async def test_a_statement_signed_elsewhere_is_stored_sealed_under_its_plain_hash(
    client_for: ClientFor,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    created = await create(client_for, world)
    signed = pdf_of([["zz", "signed", "on", "paper"]])
    resp = await (await client_for(world.expert)).post(
        world.url(f"expert-meetings/{created['id']}/signed-external"),
        content=signed,
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 200, resp.text
    assert one(admin, "select signed_sha256 from app.expert_meetings where id = %s", created["id"]) == sha(signed)
    path = trial_path(world.case, "meetings", created["id"], f"signed-{sha(signed)}.pdf")
    await opened(storage, aconn, world, path, signed)


async def test_the_court_bundle_is_stored_sealed_and_still_matched(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    path = one(admin, "select storage_path from app.court_bundles where case_id = %s", world.case)
    await opened(storage, aconn, world, path, pdf)
    assert one(admin, "select sha256 from app.court_bundles where case_id = %s", world.case) == sha(pdf)
    assert int(one(admin, "select count(*) from app.concordance where case_id = %s", world.case)) > 0


async def test_reading_after_the_case_key_is_destroyed_fails(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    world: World,
) -> None:
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    path = one(admin, "select storage_path from app.court_bundles where case_id = %s", world.case)
    async with worker_db.job(world.case) as conn:
        await CRYPTO.destroy(conn, world.case)
    async with worker_db.job(world.case) as conn:
        with pytest.raises(CaseKeyDestroyed):
            await get_sealed(current(), conn, world.case, path)


async def plain_files(admin: psycopg.Connection, storage: SupabaseStorage, w: World, meeting: str) -> dict[str, bytes]:
    """Plain PDFs at trial paths named by trial rows, as files written before sealing were."""
    signed = pdf_of([["zz", "signed", "on", "paper"]])
    bundle, _ = trial_bundle()
    files = {
        trial_path(w.case, "agendas", f"{uuid.uuid4()}.pdf"): agenda_pdf(AGENDA),
        trial_path(w.case, "meetings", meeting, f"{uuid.uuid4().hex}.pdf"): pdf_of([["zz", "prepared"]]),
        trial_path(w.case, "bundles", f"{uuid.uuid4()}.pdf"): bundle,
        trial_path(w.case, "packs", f"{uuid.uuid4()}.pdf"): pdf_of([["zz", "pack"]]),
        trial_path(w.case, "meetings", meeting, f"signed-{sha(signed)}.pdf"): signed,
    }
    for path, data in files.items():
        await storage.put(path, data)
    by_kind = {k: p for p in files for k in ("agendas", "bundles", "packs") if f"/trial/{k}/" in p}
    prepared = next(p for p in files if "/meetings/" in p and "/signed-" not in p)
    admin.execute(
        "insert into app.agenda_versions (case_id, meeting_id, version_no, storage_path, sha256, job_id)"
        " values (%s, %s, 1, %s, %s, %s)",
        (w.case, meeting, by_kind["agendas"], sha(files[by_kind["agendas"]]), uuid.uuid4()),
    )
    admin.execute(
        "update app.expert_meetings set prepared_path = %s, prepared_sha256 = %s, prepared_pages = 1,"
        " signed_sha256 = %s where id = %s",
        (prepared, sha(files[prepared]), sha(signed), meeting),
    )
    admin.execute(
        "insert into app.court_bundles (case_id, version, status, storage_path, sha256, job_id)"
        " values (%s, 1, 'matched', %s, %s, %s)",
        (w.case, by_kind["bundles"], sha(files[by_kind["bundles"]]), uuid.uuid4()),
    )
    return files


async def test_older_plain_files_are_sealed_once_and_a_second_run_changes_nothing(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    created = await create(client_for, world)
    files = await plain_files(admin, storage, world, created["id"])
    async with worker_db.job(world.case) as conn:
        first = await reseal_plain_files(conn, current(), world.case)
    assert first == 4, "agenda, prepared statement, signed-elsewhere statement and court bundle"
    named = [p for p in files if "/packs/" not in p]
    for path in named:
        await opened(storage, aconn, world, path, files[path])
    sealed = {p: await storage.get(p) for p in named}
    async with worker_db.job(world.case) as conn:
        assert await reseal_plain_files(conn, current(), world.case) == 0
    assert {p: await storage.get(p) for p in named} == sealed, "sealed files are left as they are"
    rows = admin.execute(
        "select target from app.audit_log where case_id = %s and action = 'trial.files_resealed' order by seq",
        (world.case,),
    ).fetchall()
    assert [r[0] for r in rows] == ["4"], "one audit row, a count only"


async def test_the_reseal_job_runs_for_one_case(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
    seed: Seed,
) -> None:
    from app.features.trial.jobs import reseal_trial_files

    created = await create(client_for, world)
    files = await plain_files(admin, storage, world, created["id"])
    await reseal_trial_files(
        JobContext(worker_db, seed.job(world.case, kind="reseal_trial_files"), world.case, "reseal_trial_files", 1)
    )
    path = next(p for p in files if "/bundles/" in p)
    await opened(storage, aconn, world, path, files[path])
