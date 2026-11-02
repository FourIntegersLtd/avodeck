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
import pypdf
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import CaseFileStorage, StorageError, case_prefix
from app.features.reader import repo, transcript_docx, transcript_jobs, transcript_layout, transcript_pdf
from app.features.reader.transcript_words import word
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
    monkeypatch.setattr(transcript_jobs.services, "storage", storage)
    monkeypatch.setattr(transcript_jobs.services, "crypto", crypto)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: CaseFileStorage, crypto: CaseCrypto) -> FastAPI:
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


@pytest.fixture
def real_builders() -> None:
    """Ask for this in a test that needs the real arranging and the real Word and PDF builders."""


PICTURES: list[tuple[int, bytes | None]] = []


@pytest.fixture(autouse=True)
def stand_ins(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """While a builder is still a stub, plain stand-ins let the job's own work be tested; real ones win once built."""
    PICTURES.clear()
    if "real_builders" in request.fixturenames:
        return
    empty = transcript_layout.Coverage(0, 0, 0, 0, 0)

    def stub(call: Any) -> bool:
        try:
            call()
        except NotImplementedError:
            return True
        except Exception:  # noqa: BLE001 - a real builder may dislike the probe; it is not a stub
            return False
        return False

    layout = transcript_layout.Layout([], [], [], empty)
    if stub(lambda: transcript_layout.arrange([], None, no_entry_pages=[], unread_pages=[], coverage=empty)):  # type: ignore[arg-type]

        def arrange(entries: Any, pack: Any, *, no_entry_pages: Any, unread_pages: Any, coverage: Any) -> Any:
            return transcript_layout.Layout(
                [transcript_layout.Section("entries", "transcript.section.entries", list(entries))],
                no_entry_pages,
                unread_pages,
                coverage,
            )

        monkeypatch.setattr(transcript_layout, "arrange", arrange)
    if stub(lambda: transcript_docx.build(layout, date_order="dmy", language="en", received={})):

        def docx_build(lay: Any, *, date_order: str, language: str, received: Any) -> bytes:
            document = docx.Document()
            for e in (x for sec in lay.sections for x in sec.entries):
                document.add_heading(f"Page {e.page_no}", level=2)
                for line in e.lines:
                    document.add_paragraph(line)
            for n in [*lay.no_entry_pages, *lay.unread_pages]:
                document.add_heading(f"Page {n}", level=2)
            for _ in lay.unread_pages:
                document.add_paragraph(word(language, "unread"))
            out = io.BytesIO()
            document.save(out)
            return out.getvalue()

        monkeypatch.setattr(transcript_docx, "build", docx_build)
    if stub(lambda: transcript_pdf.build_pdf(layout, [], date_order="dmy", language="en", received={})):

        def pdf_build(lay: Any, pictures: Any, *, date_order: str, language: str, received: Any) -> bytes:
            PICTURES.extend(pictures)
            return b"%PDF-1.4 stand-in " + b" ".join(e.lines[0].encode() for s in lay.sections for e in s.entries[:1])

        monkeypatch.setattr(transcript_pdf, "build_pdf", pdf_build)


FORMATS = ["docx", "pdf"]


async def run_job(worker_db: Database, job: uuid.UUID, case: uuid.UUID, fmt: str) -> None:
    if fmt == "pdf":
        await transcript_jobs.export_transcript_pdf(JobContext(worker_db, job, case, "export_transcript_pdf", 1))
    else:
        await transcript_jobs.export_transcript(JobContext(worker_db, job, case, "export_transcript", 1))


async def build(
    client: Any, worker_db: Database, admin: psycopg.Connection, w: dict[str, Any], fmt: str = "docx"
) -> uuid.UUID:
    resp = await client.post(f"/v1/cases/{w['case']}/transcript/download", json={"format": fmt})
    job = uuid.UUID(resp.json()["job_id"])
    await run_job(worker_db, job, w["case"], fmt)
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    return job


async def add_picture(
    aconn: psycopg.AsyncConnection, crypto: CaseCrypto, storage: CaseFileStorage, case: Any, page: Any
) -> None:
    """Gives a page a small sealed picture, as the pipeline would."""
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (10, 10), "white").save(out, "JPEG")
    path = f"{case_prefix(case)}/pictures/{page.page_id}.jpg"
    await storage.put(path, await crypto.encrypt(aconn, case, out.getvalue(), purpose=path))
    await aconn.execute("update app.pages set image_path = %s where id = %s", (path, page.page_id))


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
    two = await add_page(
        aconn, crypto, world["case"], doc, 2, [Spec("zz_session", ("second entry",), day=date(2020, 2, 1))]
    )
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


@pytest.mark.parametrize("fmt", FORMATS)
async def test_the_copy_is_downloaded_once_and_audited(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
    fmt: str,
) -> None:
    doc = await add_document(aconn, world["case"])
    page = await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))])
    await read(api_db, world, page)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world, fmt)
    first = await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    assert first.status_code == 200
    assert first.headers["content-type"].startswith("application/pdf" if fmt == "pdf" else "application/vnd.openxml")
    assert first.headers["content-disposition"].endswith(f'transcript-{job}.{fmt}"')
    assert (await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")).status_code == 404
    row = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.transcript_downloaded'"
        " and target = %s",
        (world["case"], f"export:{job}"),
    ).fetchone()
    assert row is not None and row[0] == 1


async def test_a_job_nobody_asked_for_builds_nothing(
    worker_db: Database, seed: Seed, storage: CaseFileStorage, world: dict[str, Any]
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
    storage: CaseFileStorage,
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


async def test_every_page_is_in_the_file(
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
    assert "readword" in joined and "unreadword" in joined


async def test_a_page_the_computer_could_not_read_is_said_so_in_the_file(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    doc = await add_document(aconn, world["case"])
    ok = await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("readword",))])
    failed = await add_page(aconn, crypto, world["case"], doc, 2, [], failed=True)
    await add_page(aconn, crypto, world["case"], doc, 3, [], failed=True)
    await read(api_db, world, ok, failed)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world)
    resp = await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    text = paragraphs(resp.content)
    assert any("look at the picture" in p for p in text)
    assert any("Page 2" in p for p in text) and any("Page 3" in p for p in text)  # all pages, read or not


async def test_the_file_is_built_before_any_page_is_read(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("unreadword",))])
    client = await client_for(world["expert"])
    resp = await client.post(f"/v1/cases/{world['case']}/transcript/download")
    assert resp.status_code == 202
    job = await build(client, worker_db, admin, world)
    got = await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    assert "unreadword" in " ".join(paragraphs(got.content))


@pytest.mark.parametrize("fmt", FORMATS)
async def test_requesting_and_downloading_records_no_read(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
    fmt: str,
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))])
    client = await client_for(world["expert"])
    count = "select count(*) from app.page_reads where case_id = %s"
    before = admin.execute(count, (world["case"],)).fetchone()
    progress = (await client.get(f"/v1/cases/{world['case']}/progress")).json()
    job = await build(client, worker_db, admin, world, fmt)
    assert (await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")).status_code == 200
    assert admin.execute(count, (world["case"],)).fetchone() == before
    assert (await client.get(f"/v1/cases/{world['case']}/progress")).json() == progress


async def test_the_word_file_has_sections_in_pack_order(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    seed: Seed,
    real_builders: None,
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    world = {"case": case, "expert": expert}
    doc = await add_document(aconn, case)
    await add_page(aconn, crypto, case, doc, 1, [Spec("zz_session", ("note",), day=date(2020, 1, 1))])
    client = await client_for(expert)
    job = await build(client, worker_db, admin, world)
    got = await client.get(f"/v1/cases/{case}/transcript/download/{job}")
    headings = [p.text for p in docx.Document(io.BytesIO(got.content)).paragraphs if p.style.name == "Heading 1"]
    assert headings and headings == sorted(headings, key=headings.index)  # the pack's order, not alphabetical


async def test_the_pdf_job_gets_a_picture_per_page_and_goes_on_without_a_missing_one(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    storage: CaseFileStorage,
    world: dict[str, Any],
) -> None:
    doc = await add_document(aconn, world["case"])
    one = await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))])
    await add_page(aconn, crypto, world["case"], doc, 2, [])  # no picture stored
    await add_picture(aconn, crypto, storage, world["case"], one)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world, "pdf")
    assert [n for n, _ in PICTURES] == [1, 2]
    assert PICTURES[0][1] is not None and PICTURES[1][1] is None
    assert (await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")).status_code == 200


async def test_the_pdf_has_a_picture_page_per_bundle_page(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    storage: CaseFileStorage,
    world: dict[str, Any],
    real_builders: None,
) -> None:
    doc = await add_document(aconn, world["case"])
    one = await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))])
    two = await add_page(aconn, crypto, world["case"], doc, 2, [])
    for page in (one, two):
        await add_picture(aconn, crypto, storage, world["case"], page)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world, "pdf")
    got = await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    reader = pypdf.PdfReader(io.BytesIO(got.content))
    assert len(reader.pages) >= 3 and reader.outline  # text, then a picture page each; bookmarks for the parts


@pytest.mark.parametrize("fmt", FORMATS)
async def test_an_organisation_admin_cannot_ask_for_the_file(
    client_for: Any, aconn: psycopg.AsyncConnection, crypto: CaseCrypto, seed: Seed, fmt: str
) -> None:
    expert, boss = seed.person(), seed.person()
    case = seed.case(seed.org(members={expert: "owner", boss: "admin"}), expert, status="active")
    doc = await add_document(aconn, case)
    await add_page(aconn, crypto, case, doc, 1, [Spec("zz_session", ("unreadword",))])
    client = await client_for(boss)
    assert (await client.post(f"/v1/cases/{case}/transcript/download", json={"format": fmt})).status_code == 403


@pytest.mark.parametrize("fmt", FORMATS)
async def test_a_storage_fault_is_not_reported_as_already_downloaded(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    storage: CaseFileStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    fmt: str,
) -> None:
    doc = await add_document(aconn, world["case"])
    await read(api_db, world, await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))]))
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world, fmt)
    real = storage.get

    async def broken(path: str) -> bytes:
        raise StorageError("storage answered 500")

    monkeypatch.setattr(storage, "get", broken)
    url = f"/v1/cases/{world['case']}/transcript/download/{job}"
    assert (await client.get(url)).status_code == 503
    monkeypatch.setattr(storage, "get", real)
    assert (await client.get(url)).status_code == 200  # the copy was not lost, nor counted as taken


@pytest.mark.parametrize("fmt", FORMATS)
async def test_the_copy_is_handed_over_once_even_if_removing_it_fails(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    storage: CaseFileStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    fmt: str,
) -> None:
    doc = await add_document(aconn, world["case"])
    await read(api_db, world, await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))]))
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world, fmt)

    async def broken(paths: Any) -> None:
        raise StorageError("storage answered 500")

    monkeypatch.setattr(storage, "delete", broken)
    url = f"/v1/cases/{world['case']}/transcript/download/{job}"
    assert (await client.get(url)).status_code == 200
    assert (await client.get(url)).status_code == 404  # the audit row, not the file, says it was taken
