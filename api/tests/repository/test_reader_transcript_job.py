"""Slice B job: `export_transcript` builds the Word copy of the records transcript (ADR 0028).

The file holds the same entries as the screen, in the same order, and prints the record as written (founder
decision of 2026-11-04, ADR 0035). It is stored sealed under the case key at `exports/<job>/transcript.docx`,
downloaded once by the person who asked, and removed by the daily close run with the other old exports. The
download is audited.
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

from app.core import deferred
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.email import OutboxMailer
from app.core.jobs import JobContext
from app.core.storage import CaseFileStorage, StorageError, case_prefix
from app.features.reader import repo, transcript, transcript_jobs, transcript_pdf
from app.features.reader.transcript_words import word
from tests.support.db import scope_for
from tests.support.deferred import drain
from tests.support.files import build_app, local_storage
from tests.support.identity import SENTENCE, record_identity
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


PICTURES: list[tuple[int, bytes | None]] = []


@pytest.fixture(autouse=True)
def capture_pictures(monkeypatch: pytest.MonkeyPatch) -> None:
    """Record the pictures the job hands the real PDF builder."""
    PICTURES.clear()
    real = transcript_pdf.build_pdf

    def build_pdf(lay: Any, pictures: Any, **kw: Any) -> bytes:
        pictures = list(pictures)
        PICTURES.extend(pictures)
        return real(lay, pictures, **kw)

    monkeypatch.setattr(transcript_pdf, "build_pdf", build_pdf)


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


async def test_the_file_holds_the_entries_in_order_as_written(
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
    assert SENTENCE in text  # names and dates of birth print as written: the founder's decision, ADR 0035
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
    assert any(p.endswith(word("en", "unread_pages")) for p in text)  # heading now carries "Appendix B"
    assert "2, 3" in text  # all pages, read or not


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
) -> None:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    world = {"case": case, "expert": expert}
    doc = await add_document(aconn, case)
    await add_page(aconn, crypto, case, doc, 1, [Spec("zz_session", ("note",), day=date(2020, 1, 1))])
    client = await client_for(expert)
    job = await build(client, worker_db, admin, world)
    got = await client.get(f"/v1/cases/{case}/transcript/download/{job}")
    headings = [
        p.text
        for p in docx.Document(io.BytesIO(got.content)).paragraphs
        if p.style is not None and p.style.name == "Heading 1"
    ]
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


@pytest.mark.parametrize("fmt", FORMATS)
async def test_the_cover_names_the_case_by_the_label_its_members_see(
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
    job = await build(client, worker_db, admin, world, fmt)
    got = await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    if fmt == "pdf":
        cover = pypdf.PdfReader(io.BytesIO(got.content)).pages[0].extract_text() or ""
    else:
        cover = "\n".join(paragraphs(got.content)[:12])
    assert "Case: Case Z" in " ".join(cover.split())


async def test_the_cover_counts_the_whole_bundle_and_every_word_read_is_a_word_printed(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = await add_document(aconn, world["case"])
    one = await add_page(
        aconn,
        crypto,
        world["case"],
        doc,
        1,
        [Spec("zz_session", ("header words",)), Spec("zz_session", ("kept note",)), Spec("zz_session", ("end",))],
    )
    for at in (0, 2):  # the header and the last word lie in no entry
        await aconn.execute("delete from app.record_entries where id = %s", (one.entry_ids[at],))
    await add_page(aconn, crypto, world["case"], doc, 2, [Spec("zz_session", ("a copy",))], duplicate_of=one.page_id)
    bare = await add_page(aconn, crypto, world["case"], doc, 3, [Spec("zz_session", ("no entry here",))])
    await aconn.execute("delete from app.record_entries where id = %s", (bare.entry_ids[0],))
    seen: list[Any] = []
    real = transcript_pdf.build_pdf

    def build_pdf(lay: Any, pictures: Any, **kw: Any) -> bytes:
        seen.append(lay)
        return real(lay, pictures, **kw)

    monkeypatch.setattr(transcript_pdf, "build_pdf", build_pdf)
    client = await client_for(world["expert"])
    await build(client, worker_db, admin, world, "pdf")
    cov = seen[0].coverage
    assert (cov.bundle_pages, cov.total_pages, cov.set_aside) == (3, 2, 1)
    assert cov.total_pages + cov.set_aside == cov.bundle_pages
    assert cov.words_read == cov.words_printed == 8  # 5 on page 1 and 3 on page 3; the copy's two are not in it
    assert seen[0].picture_words == {3: ["no entry here"]}


async def test_the_words_of_a_page_the_computer_could_not_read_are_printed_and_counted(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("fine note",))])
    smudged = await add_page(
        aconn, crypto, world["case"], doc, 2, [Spec("zz_session", ("smudged words here",))], failed=True
    )
    await aconn.execute("delete from app.record_entries where id = %s", (smudged.entry_ids[0],))
    seen: list[Any] = []
    real = transcript_pdf.build_pdf

    def build_pdf(lay: Any, pictures: Any, **kw: Any) -> bytes:
        seen.append(lay)
        return real(lay, pictures, **kw)

    monkeypatch.setattr(transcript_pdf, "build_pdf", build_pdf)
    client = await client_for(world["expert"])
    job = await build(client, worker_db, admin, world, "pdf")
    got = await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    cov = seen[0].coverage
    assert cov.words_read == cov.words_printed == 5  # two on page 1, three on the page that could not be read
    assert seen[0].unread_pages == [2] and seen[0].picture_words == {2: ["smudged words here"]}
    pages = pypdf.PdfReader(io.BytesIO(got.content)).pages
    assert "smudged words here" in " ".join(" ".join((p.extract_text() or "") for p in pages).split())


async def test_a_file_that_leaves_out_words_the_computer_read_is_never_built(
    client_for: Any,
    worker_db: Database,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("fine note",))])
    bare = await add_page(aconn, crypto, world["case"], doc, 2, [Spec("zz_session", ("never printed",))])
    await aconn.execute("delete from app.record_entries where id = %s", (bare.entry_ids[0],))
    real = transcript.entries

    async def forgetful(*a: Any, **kw: Any) -> Any:
        got = await real(*a, **kw)
        got.picture_words.clear()  # the words of the page with no entries are not handed on
        return got

    monkeypatch.setattr(transcript, "entries", forgetful)
    client = await client_for(world["expert"])
    resp = await client.post(f"/v1/cases/{world['case']}/transcript/download", json={"format": "docx"})
    job = uuid.UUID(resp.json()["job_id"])
    with pytest.raises(ValueError):
        await run_job(worker_db, job, world["case"], "docx")


async def _stored(storage: CaseFileStorage, case: Any, job: uuid.UUID, fmt: str) -> bool:
    try:
        await storage.get(transcript_jobs.transcript_path(case, job, fmt))
    except StorageError:
        return False
    return True


async def _built_and_taken(
    api_db: Database,
    client: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    world: dict[str, Any],
    fmt: str,
) -> uuid.UUID:
    doc = await add_document(aconn, world["case"])
    await read(api_db, world, await add_page(aconn, crypto, world["case"], doc, 1, [Spec("zz_session", ("note",))]))
    return await build(client, worker_db, admin, world, fmt)


@pytest.mark.parametrize("fmt", FORMATS)
async def test_the_stored_copy_goes_when_the_worker_delivers_not_when_the_request_ends(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    storage: CaseFileStorage,
    world: dict[str, Any],
    fmt: str,
) -> None:
    """BG-16: removing the copy is durable work, so an API restart after the commit cannot leave it behind."""
    admin.execute("delete from app.deferred_work")
    client = await client_for(world["expert"])
    job = await _built_and_taken(api_db, client, worker_db, admin, aconn, crypto, world, fmt)
    assert (await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")).status_code == 200
    assert await _stored(storage, world["case"], job, fmt)  # the request only wrote the work down
    assert await drain(worker_dsn, OutboxMailer()) == 1
    assert not await _stored(storage, world["case"], job, fmt)


async def test_a_download_that_rolls_back_removes_nothing(
    api_db: Database,
    client_for: Any,
    worker_db: Database,
    worker_dsn: str,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    storage: CaseFileStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.features.reader import transcript_routes

    admin.execute("delete from app.deferred_work")
    client = await client_for(world["expert"])
    job = await _built_and_taken(api_db, client, worker_db, admin, aconn, crypto, world, "docx")

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("the request fails after the file was taken")

    monkeypatch.setattr(transcript_routes, "iter_chunks", broken)
    try:
        await client.get(f"/v1/cases/{world['case']}/transcript/download/{job}")
    except RuntimeError:
        pass  # the test transport re-raises the server error
    row = admin.execute("select count(*) from app.deferred_work where name = 'reader.transcript_remove'").fetchone()
    assert row is not None and row[0] == 0  # the work was written in the request's transaction, which rolled back
    assert await drain(worker_dsn, OutboxMailer()) == 0
    assert await _stored(storage, world["case"], job, "docx")


@pytest.mark.parametrize("fmt", FORMATS)
async def test_removing_the_copy_twice_is_harmless(
    worker_db: Database, seed: Seed, storage: CaseFileStorage, world: dict[str, Any], fmt: str
) -> None:
    job = seed.job(world["case"], kind="export_transcript")
    path = transcript_jobs.transcript_path(world["case"], job, fmt)
    await storage.put(path, b"sealed")
    args = {"case_id": str(world["case"]), "job_id": str(job), "fmt": fmt}
    handler = deferred.calls["reader.transcript_remove"]
    async with worker_db.anonymous() as conn:
        await handler(conn, args)
        assert not await _stored(storage, world["case"], job, fmt)
        await handler(conn, args)  # already gone: success, not an error


async def test_a_storage_fault_while_removing_is_left_for_the_next_delivery(
    worker_db: Database,
    seed: Seed,
    storage: CaseFileStorage,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = seed.job(world["case"], kind="export_transcript")

    async def broken(paths: Any) -> None:
        raise StorageError("storage answered 500")

    monkeypatch.setattr(storage, "delete", broken)
    args = {"case_id": str(world["case"]), "job_id": str(job), "fmt": "docx"}
    async with worker_db.anonymous() as conn:
        with pytest.raises(StorageError):
            await deferred.calls["reader.transcript_remove"](conn, args)
