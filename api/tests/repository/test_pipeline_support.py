"""Shared pieces for slice A's tests (this module holds no tests).

`Harness` runs the real pipeline end to end on the local database: files go up through the upload
routes (so the `DocumentUploaded` subscriber queues the work), a worker on its own queue runs the
jobs, and the stand-ins replay recorded OCR and model answers. Everything here is synthetic: the
recorded pages come from the generator's invented bundle (`evals/`), the rest is made up here.
Nothing in this module prints or asserts on page text (rule 6); messages name pages only.
"""

from __future__ import annotations

import contextlib
import functools
import hashlib
import io
import json
import shutil
import uuid
from collections.abc import AsyncIterator, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pikepdf
import psycopg

from app.core.ai.answers import AnswerBook
from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.ai.ocr import OcrModel, OcrResult, RecordedOcr
from app.core.ai.provenance import atoms
from app.core.ai.recordings import FixtureStore, RecordingMissing
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.jobs import JobRegistry
from app.core.pdf import split_pages
from app.core.scan import RecordedScanner
from app.core.storage import SupabaseStorage
from tests.support.ai import FIXTURES, ScriptedBedrock, cell, response
from tests.support.db import ClientFor
from tests.support.files import CHUNK, build_app
from tests.support.jobs import FINISHED, isolated_registry, open_worker, run_until_finished
from tests.support.packs import REPO
from tests.support.uploads import run_finalize

ROOT = FIXTURES / "pipeline"
EVAL = REPO / "evals" / "gb-eaw" / "clin-neg-breach" / "general-practice"
BUNDLE = EVAL / "bundles" / "synthetic-mrs-a"
EXPECTED = EVAL / "expected"
A4 = (595.0, 842.0)  # points


# --- the committed 18-page subset ------------------------------------------------------------------


@functools.cache
def manifest() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((ROOT / "manifest.json").read_text())
    return data


def subset_entries() -> list[dict[str, Any]]:
    return list(manifest()["pages"])


def subset_numbers() -> list[int]:
    """The subset's pages by their number in the full bundle, in upload order."""
    return [int(p["page"]) for p in subset_entries()]


def subset_page_bytes() -> list[bytes]:
    return [(ROOT / str(p["file"])).read_bytes() for p in subset_entries()]


def merged(pages: Sequence[bytes]) -> bytes:
    """One PDF holding these pages in order."""
    out = pikepdf.new()
    for page in pages:
        with pikepdf.open(io.BytesIO(page)) as src:
            out.pages.extend(src.pages)
            buf = io.BytesIO()
            out.save(buf)
        out = pikepdf.open(io.BytesIO(buf.getvalue()))
    buf = io.BytesIO()
    out.save(buf)
    return buf.getvalue()


@functools.cache
def subset_document() -> bytes:
    return merged(subset_page_bytes())


def rekeyed_store(dest: Path, document: bytes, originals: Sequence[bytes], source: Path = ROOT) -> FixtureStore:
    """The recordings under `source`, with each OCR recording keyed by the bytes core `split_pages` cuts
    from `document` (a PDF of `originals` in order), so the pipeline finds them. Answers are keyed by
    a page's words, which do not change, and are copied as they are."""
    shutil.copytree(source / "answer", dest / "answer")
    original = FixtureStore(source)
    store = FixtureStore(dest)
    pages = split_pages(document)
    assert len(pages) == len(originals)
    for new, old in zip(pages, originals, strict=True):
        try:
            recorded = original.lookup("ocr", RecordedOcr.request(old, "layout"))
        except RecordingMissing:
            continue
        store.record("ocr", RecordedOcr.request(new, "layout"), recorded)
    return store


# --- made-up files ---------------------------------------------------------------------------------


def text_pdf(lines_per_page: Sequence[Sequence[str]]) -> bytes:
    """A born-digital PDF: one page per entry, each line typed in Helvetica."""
    pdf = pikepdf.new()
    font = pdf.make_indirect(
        pikepdf.Dictionary(Type=pikepdf.Name.Font, Subtype=pikepdf.Name.Type1, BaseFont=pikepdf.Name.Helvetica)
    )
    for lines in lines_per_page:
        ops = "".join(f"BT /F1 12 Tf 72 {760 - 20 * i} Td ({line}) Tj ET\n" for i, line in enumerate(lines))
        page = pikepdf.Page(
            pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=[0, 0, *A4],
                Resources=pikepdf.Dictionary(Font=pikepdf.Dictionary(F1=font)),
                Contents=pdf.make_stream(ops.encode()),
            )
        )
        pdf.pages.append(page)
    out = io.BytesIO()
    pdf.save(out)
    return out.getvalue()


def eicar() -> bytes:
    """The standard antivirus test file (built from parts so this file is not itself flagged)."""
    return ("X5O!P%@AP[4\\PZX54(P^)7CC)7}$" + "EICAR-STANDARD-ANTIVIRUS-TEST-FILE!$H+H*").encode()


# --- stand-ins -------------------------------------------------------------------------------------


class CountingOcr:
    """Recorded OCR that remembers which pages (by SHA-256) were sent to it."""

    def __init__(self, inner: RecordedOcr) -> None:
        self.inner = inner
        self.endpoint = inner.endpoint
        self.sent: list[str] = []

    async def analyse(self, document: bytes, model: OcrModel = "layout") -> OcrResult:
        self.sent.append(hashlib.sha256(document).hexdigest())
        return await self.inner.analyse(document, model)


@functools.cache
def recorded_source_keys() -> dict[str, str]:
    """The generator records each page's document type; the pack's source key for it, read from the
    expected files (so no source word is written into a test)."""
    pages = {int(p["page"]): p["source_type"] for p in json.loads((EXPECTED / "pages.json").read_text())}
    keys = {int(p["page"]): p["source_type"] for p in json.loads((EXPECTED / "sources.json").read_text())}
    found: dict[str, str] = {}
    for n, doc_type in pages.items():
        found.setdefault(doc_type, keys[n])
    return found


class PackSourceBook:
    """The answer book, with each recorded T-07b `source_type` given as the pack's source key."""

    def __init__(self, inner: AnswerBook, keys: Mapping[str, str]) -> None:
        self.inner = inner
        self.keys = dict(keys)
        self.endpoint = inner.endpoint

    @property
    def requests(self) -> list[ModelRequest]:
        return self.inner.requests

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        answer = await self.inner.invoke(request)
        output = dict(answer.output)
        if request.task_id == "T-07b" and isinstance(output.get("source_type"), str):
            output["source_type"] = self.keys.get(output["source_type"], output["source_type"])
        return ModelResponse(output=output, input_tokens=answer.input_tokens, output_tokens=answer.output_tokens)


def empty_answer(source_key: str) -> ModelResponse:
    """A valid T-07b answer that finds nothing on the page."""
    return response(
        {
            "source_type": source_key,
            "printed_dates": [],
            "facts": [],
            "references_out": [],
            "availability_evidence": [],
            "actions_requested_of_clinician": [],
            "entries": [],
        }
    )


def recorded_book(store: FixtureStore) -> PackSourceBook:
    return PackSourceBook(AnswerBook(cell(), store), recorded_source_keys())


# --- the harness -----------------------------------------------------------------------------------


@dataclass
class Harness:
    admin: psycopg.Connection
    api_db: Database
    worker_db: Database
    worker_dsn: str
    client: httpx.AsyncClient
    expert: uuid.UUID
    case_id: uuid.UUID
    storage: SupabaseStorage
    crypto: CaseCrypto
    registry: JobRegistry
    ocr: CountingOcr | None
    bedrock: Any
    names: list[str] = field(default_factory=list)

    async def upload(self, data: bytes, *, filename: str = "records.pdf", append: bool = False) -> uuid.UUID:
        body = {
            "kind": "records",
            "filename": filename,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "append": append,
        }
        started = await self.client.post(f"/v1/cases/{self.case_id}/uploads", json=body)
        assert started.status_code == 201, started.status_code
        upload_id = started.json()["upload_id"]
        for n, at in enumerate(range(0, len(data), CHUNK), start=1):
            sent = await self.client.put(
                f"/v1/uploads/{upload_id}/chunks/{n}",
                content=data[at : at + CHUNK],
                headers={
                    "content-type": "application/octet-stream",
                    "x-chunk-sha256": hashlib.sha256(data[at : at + CHUNK]).hexdigest(),
                },
            )
            assert sent.status_code == 200, sent.status_code
        done = await self.client.post(f"/v1/uploads/{upload_id}/complete", json={"sha256": body["sha256"]})
        assert done.status_code == 202, done.status_code
        await run_finalize(self.worker_dsn, self.admin, upload_id, self.storage, self.crypto, finish=True)
        status = await self.client.get(f"/v1/uploads/{upload_id}")
        assert status.status_code == 200, status.text
        document = status.json()["document_id"]
        assert document is not None, status.text
        return uuid.UUID(document)

    def job_ids(self) -> list[uuid.UUID]:
        return [r[0] for r in self.admin.execute("select id from app.jobs where case_id = %s", (self.case_id,))]

    async def drain(self, timeout: float = 120) -> dict[uuid.UUID, str]:
        """Run the worker until every job on the case (and any job they queue) has finished."""
        async with open_worker(self.registry, self.worker_db, self.worker_dsn) as worker:
            while True:
                ids = self.job_ids()
                found = await run_until_finished(worker, self.admin, ids, timeout=timeout)
                if len(self.job_ids()) == len(ids) and all(s in FINISHED for s in found.values()):
                    return found

    async def enqueue(self, kind: str) -> uuid.UUID:
        from tests.support.db import scope_for

        async with self.api_db.request(scope_for(self.expert)) as conn:
            return await self.registry.enqueue(conn, self.case_id, kind)

    def pages(self) -> list[dict[str, Any]]:
        cur = self.admin.cursor(row_factory=psycopg.rows.dict_row)
        return cur.execute(
            "select * from app.pages where case_id = %s order by bundle_page_no", (self.case_id,)
        ).fetchall()

    async def decrypt(self, blob: bytes, purpose: str) -> bytes:
        from tests.support.db import scope_for

        async with self.api_db.request(scope_for(self.expert)) as conn:
            return await self.crypto.decrypt(conn, self.case_id, blob, purpose=purpose)


@contextlib.asynccontextmanager
async def harness(
    *,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    client_for: ClientFor | None,
    storage: SupabaseStorage,
    expert: uuid.UUID,
    case_id: uuid.UUID,
    ocr_store: FixtureStore | None = None,
    bedrock: Any = None,
    names: Sequence[str] = (),
    dates_of_birth: Sequence[Any] = (),
    embedder: Any = None,
    wired_identity: bool = False,
) -> AsyncIterator[Harness]:
    """The pipeline wired to stand-ins, its job kinds on a registry of their own."""
    from app.core.packs import default_library
    from app.features import pipeline
    from app.features.pipeline import wiring as wiring_module
    from app.features.pipeline.wiring import Identity, PipelineServices

    crypto = CaseCrypto(LocalKeyProvider.generate())
    registry = isolated_registry()
    pipeline.register(registry)
    ocr = CountingOcr(RecordedOcr(cell(), ocr_store)) if ocr_store is not None else None
    if bedrock is None:
        bedrock = ScriptedBedrock([RuntimeError("no model answer was planned for this test")])

    async def identity(conn: Any, case: uuid.UUID) -> Identity:
        return Identity(names=tuple(names), dates_of_birth=tuple(dates_of_birth))

    services = PipelineServices(
        storage=storage,
        crypto=crypto,
        scanner=RecordedScanner(),
        ocr=ocr,
        kit=AiKit(cell(), bedrock),
        packs=default_library(),
        registry=registry,
        identity=wiring_module.identity_with(crypto) if wired_identity else identity,
        **({"embedder": embedder} if embedder is not None else {}),
    )
    app = build_app(api_db, auth_env, storage=storage, crypto=crypto)
    pipeline.mount_on(app)
    worker_db = Database(worker_dsn, max_size=4)
    await worker_db.open()
    from tests.support.seed import Seed

    session = Seed(admin).session(expert)
    from tests.support.auth import CSRF_HEADER, cookie_header

    headers = {"cookie": cookie_header(session), CSRF_HEADER: session.csrf}
    try:
        with pipeline.wiring.use(services):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="https://test", headers=headers
            ) as client:
                yield Harness(
                    admin=admin,
                    api_db=api_db,
                    worker_db=worker_db,
                    worker_dsn=worker_dsn,
                    client=client,
                    expert=expert,
                    case_id=case_id,
                    storage=storage,
                    crypto=crypto,
                    registry=registry,
                    ocr=ocr,
                    bedrock=bedrock,
                    names=list(names),
                )
    finally:
        await worker_db.close()


# --- scoring against the expected files ------------------------------------------------------------


def expected(name: str) -> Any:
    return json.loads((EXPECTED / name).read_text())


def fact_key(page: int, kind: str, value: str) -> tuple[int, str, tuple[str, ...]]:
    return (page, kind, tuple(atoms(value)))


def precision(found: Iterable[tuple[int, str, str]], wanted: Iterable[Mapping[str, Any]]) -> tuple[float, int]:
    """Share of found facts that match an expected fact (`expected/facts.json` rows) of the same kind on
    the same page, each expected fact once. A found value is copied from the page's words (rule 2), so
    it may match the expected fact's `quote` (the words as printed) or its `value` (a reading of them)."""
    wanted = list(wanted)
    found = list(found)
    used = [False] * len(wanted)
    index: dict[tuple[int, str, tuple[str, ...]], list[int]] = {}
    for i, w in enumerate(wanted):
        for text in {str(w.get("value") or ""), str(w.get("quote") or "")}:
            key = fact_key(int(w["page"]), str(w["kind"]), text)
            if key[2]:
                index.setdefault(key, []).append(i)
    hits = 0
    for f in found:
        for i in index.get(fact_key(*f), []):
            if not used[i]:
                used[i] = True
                hits += 1
                break
    return (hits / len(found) if found else 0.0), len(found)


# --- extraction (T-07b) ----------------------------------------------------------------------------


def t07b_requests(h: Harness) -> list[ModelRequest]:
    return [r for r in h.bedrock.requests if r.task_id == "T-07b"]


def facts_of(h: Harness) -> list[dict[str, Any]]:
    """The case's facts with their page numbers."""
    cur = h.admin.cursor(row_factory=psycopg.rows.dict_row)
    return cur.execute(
        "select f.*, p.bundle_page_no from app.facts f join app.pages p on p.id = f.page_id"
        " where f.case_id = %s order by p.bundle_page_no, f.created_at",
        (h.case_id,),
    ).fetchall()


async def page_words(h: Harness, page_id: uuid.UUID) -> dict[str, tuple[str, bool]]:
    """A page's words (opened) with whether each is struck through, in reading order."""
    from app.features.pipeline.paths import word_purpose

    rows = h.admin.execute(
        "select word_id, text, struck from app.ocr_words where page_id = %s order by n", (page_id,)
    ).fetchall()
    out: dict[str, tuple[str, bool]] = {}
    for word_id, sealed, struck in rows:
        out[word_id] = ((await h.decrypt(bytes(sealed), word_purpose(page_id, word_id))).decode(), bool(struck))
    return out


def from_struck_text(value: str, phrases: Iterable[str]) -> bool:
    """Whether a value is (part of) a crossed-out phrase, or holds one."""
    found = atoms(value)
    for phrase in phrases:
        crossed = atoms(phrase)
        if not found or not crossed:
            continue
        small, large = sorted((found, crossed), key=len)
        if any(large[i : i + len(small)] == small for i in range(len(large) - len(small) + 1)):
            return True
    return False


def identifier_leaked(texts: Iterable[str], case: Mapping[str, Any], recognise: Any) -> bool:
    """Whether any text sent to a model holds a case name, the case's identifier numbers or postcode,
    or anything the pack's recognisers find. A bool, so nothing is printed."""
    singles = [str(case[k]) for k in ("nhs_number", "postcode") if case.get(k)]
    for text in texts:
        folded = text.casefold()
        squashed = "".join(folded.split())
        if any(name.casefold() in folded for name in case.get("case_names", [])):
            return True
        if any("".join(s.casefold().split()) in squashed for s in singles):
            return True
        if recognise(text):
            return True
    return False


def schema_enums(schema: Any) -> list[set[str]]:
    """Every `enum` list (and `const` value) in a JSON schema, as sets."""
    found: list[set[str]] = []
    if isinstance(schema, Mapping):
        if isinstance(schema.get("enum"), list):
            found.append({str(v) for v in schema["enum"] if v is not None})
        for value in schema.values():
            found.extend(schema_enums(value))
    elif isinstance(schema, list):
        for value in schema:
            found.extend(schema_enums(value))
    return found


def expected_dates() -> dict[tuple[int, str], str | None]:
    """Each expected fact's date, keyed by (page, value) and (page, quote): the date a person reads."""
    out: dict[tuple[int, str], str | None] = {}
    for row in expected("facts.json"):
        for text in (row["quote"], row["value"]):
            out.setdefault((int(row["page"]), str(text)), row["event_date"])
    return out


@dataclass(frozen=True)
class DateScore:
    printed: int  # facts given a printed date, among those with an expected date
    right: int  # of those, the date a person reads
    flagged: int  # facts marked please check
    wanted: int  # facts found that have an expected date

    @property
    def accuracy(self) -> float:
        return self.right / self.printed if self.printed else 0.0

    @property
    def coverage(self) -> float:
        return self.right / self.wanted if self.wanted else 0.0


def date_score(found: Iterable[tuple[int, str, Any, str | None]]) -> DateScore:
    """Score (page, value, event_date, date_basis) rows against the expected facts' dates."""
    want = expected_dates()
    printed = right = flagged = wanted = 0
    for page, value, event_date, basis in found:
        key = (page, value)
        if key not in want or want[key] is None:
            continue
        wanted += 1
        if basis == "please_check":
            flagged += 1
        elif basis == "printed":
            printed += 1
            right += event_date is not None and event_date.isoformat() == want[key]
    return DateScore(printed=printed, right=right, flagged=flagged, wanted=wanted)
