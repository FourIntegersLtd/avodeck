"""A served case for slice G's tests, and the stand-ins its jobs use.

The case has the named expert and the other case roles, an outsider, three numbered pages the expert has read
(with their OCR words), a verified fact on each, one confirmed question and a served report version with four
paragraphs citing those facts. Documents the jobs read (questions from the other side, the other expert's
report) are stored as slice A and core store them: sealed parts under the document's storage path.

`TextOcr` reads such a document as plain text (pages split by form feeds, lines by new lines). `Scripted`
answers each AI task from a function of the request, and keeps every request. Words, values and paragraphs are
short made-up words; nothing here is a real record. Nothing here is collected as a test (no `test_` functions).
"""

from __future__ import annotations

import hashlib
import re
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from psycopg import AsyncConnection

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.ai.ocr import OcrPage, OcrResult, OcrWord, word_id
from app.core.contracts.document_words import word_purpose
from app.core.db import Database
from app.core.email import OutboxMailer
from app.core.packs import MergedPack, default_library
from app.core.storage import CaseFileStorage, case_prefix
from tests.fixtures.report.world import CRYPTO, Reads, app_for
from tests.support.ai import CELL_ENV, cell
from tests.support.contracts import add_fact, add_question
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

__all__ = ["CRYPTO", "Reads", "app_for"]

SERVED_ON = date(2026, 3, 2)
PAGE_TEXT = {
    1: "Plan review 3/52 if no better Bloods arranged today",
    2: "Ferritin 9 ug/L Haemoglobin 118 g/L Iron started",
    3: "Seen again in clinic Advised to call back if worse",
}
PARAGRAPHS = (
    # (para_no, text, indexes of the facts it cites)
    (1, "The plan was written down [1].", (0,)),
    (2, "The result was low [2].", (1,)),
    (3, "Taken together the care fell short [1][2].", (0, 1)),
    (4, "The later visit is noted [3].", (2,)),
)
PARAGRAPH_PURPOSE = "report_paragraph"
CITE_VALUE_PURPOSE = "report_citation_value"
CITE_QUOTE_PURPOSE = "report_citation_quote"


@dataclass
class World:
    combo: Combo
    org: uuid.UUID
    case: uuid.UUID
    expert: uuid.UUID
    co_expert: uuid.UUID
    assistant: uuid.UUID
    viewer: uuid.UUID
    outsider: uuid.UUID
    records: uuid.UUID
    pages: list[uuid.UUID]
    facts: list[uuid.UUID]
    question: uuid.UUID
    served: uuid.UUID | None = None
    paragraphs: list[uuid.UUID] = field(default_factory=list)

    def url(self, tail: str) -> str:
        return f"/v1/cases/{self.case}/{tail}"


def page_words(text: str) -> list[str]:
    return text.split()


async def add_words(
    admin: psycopg.Connection, aconn: AsyncConnection, case: uuid.UUID, page: uuid.UUID, text: str
) -> None:
    """The page's OCR words as slice A stores them: `w1`, `w2`, ... each sealed under its own purpose."""
    for n, word in enumerate(page_words(text), start=1):
        sealed = await CRYPTO.encrypt(aconn, case, word.encode(), purpose=word_purpose(page, f"w{n}"))
        admin.execute(
            "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
            " values (%s, %s, %s, %s, %s, array[0,0,1,0,1,1,0,1]::real[], 0.99, 'ocr')",
            (page, case, n, f"w{n}", sealed),
        )


async def build(seed: Seed, admin: psycopg.Connection, aconn: AsyncConnection, combo: Combo, reads: Reads) -> World:
    expert, co_expert, assistant, viewer, outsider = (seed.person() for _ in range(5))
    org = seed.org(
        kind="team", members={expert: "expert", co_expert: "expert", assistant: "assistant", viewer: "expert"}
    )
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(
        org,
        expert,
        status="active",
        members={co_expert: "co_expert", assistant: "assistant", viewer: "viewer"},
        packs=combo.case_row(),
    )
    admin.execute("update app.cases set instructing_party_type = 'lawyer' where id = %s", (case,))
    records = seed.document(case)
    pages = [seed.page(case, records, n) for n in (1, 2, 3)]
    facts: list[uuid.UUID] = []
    values = ("review 3/52 if no better", "Ferritin 9 ug/L", "Seen again in clinic")
    for n, page in enumerate(pages, start=1):
        await add_words(admin, aconn, case, page, PAGE_TEXT[n])
        reads.rows.add((case, page, n, expert))
        words = page_words(PAGE_TEXT[n])
        value = values[n - 1]
        start = _find(words, value.split())
        ids = [f"w{i + 1}" for i in range(start, start + len(value.split()))]
        facts.append(add_fact(admin, case, page, status="verified", value=value, word_ids=ids))
    return World(
        combo=combo,
        org=org,
        case=case,
        expert=expert,
        co_expert=co_expert,
        assistant=assistant,
        viewer=viewer,
        outsider=outsider,
        records=records,
        pages=pages,
        facts=facts,
        question=add_question(admin, case, status="confirmed"),
    )


def _find(words: Sequence[str], phrase: Sequence[str]) -> int:
    for i in range(len(words) - len(phrase) + 1):
        if [w.lower() for w in words[i : i + len(phrase)]] == [p.lower() for p in phrase]:
            return i
    raise ValueError("phrase not on the page")


async def make_version(
    admin: psycopg.Connection, aconn: AsyncConnection, w: World, *, status: str = "served", served_on: date = SERVED_ON
) -> uuid.UUID:
    """The case's report as slice F leaves it: four paragraphs with citations and both files' snapshots,
    locked with its hash and (by default) served with one serving."""
    version = uuid.uuid4()
    admin.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name, docx_path, pdf_path)"
        " values (%s, %s, 1, 'main', 'Report 1', %s, %s)",
        (
            version,
            w.case,
            f"{case_prefix(w.case)}/report/{version}/report.docx",
            f"{case_prefix(w.case)}/report/{version}/report.pdf",
        ),
    )
    w.paragraphs = []
    for para_no, text, cites in PARAGRAPHS:
        pid = uuid.uuid4()
        body = await CRYPTO.encrypt(aconn, w.case, text.encode(), purpose=PARAGRAPH_PURPOSE)
        admin.execute(
            "insert into app.report_paragraphs (id, case_id, report_version_id, para_no, section, content_control_id,"
            " text, question_id) values (%s, %s, %s, %s, 'opinion', %s, %s, %s)",
            (pid, w.case, version, para_no, f"para-{para_no}", body, w.question if para_no == 3 else None),
        )
        for cite_no, index in enumerate(cites, start=1):
            value = await CRYPTO.encrypt(aconn, w.case, b"value", purpose=CITE_VALUE_PURPOSE)
            quote = await CRYPTO.encrypt(aconn, w.case, b"quote", purpose=CITE_QUOTE_PURPOSE)
            admin.execute(
                "insert into app.citations (case_id, report_version_id, paragraph_id, cite_no, fact_id, fact_version,"
                " value_snapshot, quote_verbatim, page_id) values (%s, %s, %s, %s, %s, 1, %s, %s, %s)",
                (w.case, version, pid, cite_no, w.facts[index], value, quote, w.pages[index]),
            )
        w.paragraphs.append(pid)
    for kind in ("docx", "pdf"):
        admin.execute(
            "insert into app.report_snapshots (case_id, report_version_id, kind, content_ref, sha256)"
            " values (%s, %s, %s, %s, %s)",
            (w.case, version, kind, f"report.{kind}", hashlib.sha256(kind.encode()).hexdigest()),
        )
    admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("c" * 64, version))
    if status == "served":
        admin.execute("update app.report_versions set status = 'served' where id = %s", (version,))
        admin.execute(
            "insert into app.report_servings (case_id, report_version_id, served_on, sent_via)"
            " values (%s, %s, %s, 'email')",
            (w.case, version, served_on),
        )
        admin.execute("update app.cases set status = 'served' where id = %s", (w.case,))
    w.served = version
    return version


def served_state(admin: psycopg.Connection, version: uuid.UUID) -> tuple[Any, ...]:
    """Everything that makes up a served version, to show it never changes (rule 8)."""
    row = admin.execute(
        "select status, sha256, locked_at, docx_path, pdf_path, updated_at from app.report_versions where id = %s",
        (version,),
    ).fetchone()
    paragraphs = admin.execute(
        "select id, para_no, text from app.report_paragraphs where report_version_id = %s order by para_no", (version,)
    ).fetchall()
    snapshots = admin.execute(
        "select kind, sha256 from app.report_snapshots where report_version_id = %s order by kind", (version,)
    ).fetchall()
    return (row, [tuple(p) for p in paragraphs], [tuple(s) for s in snapshots])


async def store_document(
    storage: CaseFileStorage, admin: psycopg.Connection, aconn: AsyncConnection, case: uuid.UUID, text: str, kind: str
) -> uuid.UUID:
    """A document the expert uploaded, stored as core stores uploads: sealed parts under its storage path."""
    document = uuid.uuid4()
    prefix = f"{case_prefix(case)}/documents/{document}"
    data = text.encode()
    path = f"{prefix}/part-00001"
    await storage.put(path, await CRYPTO.encrypt(aconn, case, data, purpose=path))
    admin.execute(
        "insert into app.documents (id, case_id, kind, filename, storage_path, sha256) values (%s, %s, %s, %s, %s, %s)",
        (document, case, kind, f"{kind}.pdf", prefix, hashlib.sha256(data).hexdigest()),
    )
    return document


class TextOcr:
    """Reads a stored text document: pages split by form feeds, lines by new lines, words by spaces."""

    def __init__(self) -> None:
        self.endpoint = CELL_ENV["AZURE_DI_ENDPOINT"]
        self.calls: list[str] = []

    async def analyse(self, document: bytes, model: str = "layout") -> OcrResult:
        self.calls.append(model)
        pages = []
        for number, page in enumerate(document.decode().split("\f"), start=1):
            words: list[OcrWord] = []
            for line_no, line in enumerate(page.split("\n")):
                for col, text in enumerate(line.split()):
                    y = float(line_no * 10)
                    x = float(col * 10)
                    words.append(
                        OcrWord(word_id(number, len(words) + 1), text, (x, y, x + 8, y, x + 8, y + 8, x, y + 8), 0.99)
                    )
            pages.append(OcrPage(number=number, width=1000.0, height=1000.0, words=tuple(words)))
        return OcrResult(model=model, pages=tuple(pages))


_TOKEN = re.compile(r"\{([^}]+)\}(\S*)")


def tokens(text: str) -> list[tuple[str, str]]:
    """(word id, word) of every `{id}word` in a prompt, in order."""
    return [(m.group(1), m.group(2)) for m in _TOKEN.finditer(text)]


def ids_for(text: str, phrase: str, *, after: str | None = None) -> list[str]:
    """The ids of the first run of `{id}word` tokens spelling `phrase` (after the line containing `after`)."""
    if after is not None:
        text = text[text.index(after) :]
    found = tokens(text)
    words = [_plain(t) for _, t in found]
    want = [_plain(p) for p in phrase.split()]
    for i in range(len(words) - len(want) + 1):
        if words[i : i + len(want)] == want:
            return [found[j][0] for j in range(i, i + len(want))]
    raise ValueError("phrase not in the prompt")


def _plain(word: str) -> str:
    return re.sub(r"[^\w/]", "", word.lower())


Answer = Callable[[ModelRequest], dict[str, Any]]


class Scripted:
    """The model stand-in: answers each task id with a function of the request, keeping every request."""

    def __init__(self, answers: dict[str, Answer] | None = None) -> None:
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]
        self.answers: dict[str, Answer] = dict(answers or {})
        self.requests: list[ModelRequest] = []

    def by_task(self, task_id: str) -> list[ModelRequest]:
        return [r for r in self.requests if r.task_id == task_id]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        answer = self.answers.get(str(request.task_id))
        output = answer(request) if answer is not None else {}
        return ModelResponse(output=output, input_tokens=100, output_tokens=10)


def app_with(
    api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: CaseFileStorage
) -> FastAPI:
    """The API as slice F's tests build it, with slice G's start-up hook run (tests do not run the lifespan)."""
    from app.features.after_serving import on_startup

    app = app_for(api_db, auth_env, combo, reads, storage)
    on_startup(app)
    return app


# --- fixtures (test modules import these; pytest finds them in the module's names) ----------------------------


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    chosen: Combo = request.param
    return chosen


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: CaseFileStorage) -> FastAPI:
    return app_with(api_db, auth_env, combo, reads, storage)


@pytest.fixture
def model() -> Scripted:
    return Scripted()


@pytest.fixture
def ocr() -> TextOcr:
    return TextOcr()


@pytest.fixture
def mailer() -> OutboxMailer:
    return OutboxMailer()


@pytest.fixture(autouse=True)
def services(
    reads: Reads, storage: CaseFileStorage, model: Scripted, ocr: TextOcr, mailer: OutboxMailer
) -> Iterator[None]:
    from app.features.after_serving.services import AfterServingServices, configure

    configure(
        AfterServingServices(
            crypto=CRYPTO, storage=storage, reads=reads, kit=AiKit(cell(), model), ocr=ocr, mailer=mailer
        )
    )
    yield
    configure(None)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[AsyncConnection]:
    async with await AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
async def world(
    seed: Seed, admin: psycopg.Connection, aconn: AsyncConnection, combo: Combo, reads: Reads
) -> AsyncIterator[World]:
    built = await build(seed, admin, aconn, combo, reads)
    yield built
    # The daily reminder scan reads every case; close this case's deadlines so later tests start clean.
    admin.execute(
        "update app.deadlines set done_at = now() where case_id = %s and done_at is null and cancelled_at is null",
        (built.case,),
    )


@pytest.fixture
async def served(admin: psycopg.Connection, aconn: AsyncConnection, world: World) -> World:
    await make_version(admin, aconn, world)
    return world


async def run_job(worker_db: Database, w: World, queued: Any, kind: str) -> None:
    """Run the queued job (a 202 response) as the worker would."""
    from app.core.jobs import JobContext, registry

    assert queued.status_code == 202, queued.text
    handler = registry.kinds[kind].handler
    await handler(JobContext(worker_db, uuid.UUID(queued.json()["job_id"]), w.case, kind, 1))


def pack_of(combo: Combo) -> MergedPack:
    return default_library().combine(*combo.codes)


def classifications(combo: Combo) -> list[str]:
    return list(combo.raw("jurisdiction", "pack.yaml")["written_question_classifications"])
