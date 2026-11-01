"""A served case for slice I's tests: the named expert and other case roles, an outsider, a bundle of
31 numbered pages (101 to 130 and 214) the expert has read, each with sealed words as slice A stores
them, a served report (slice F's rows) whose paragraphs cite twelve of those pages, and verified facts.

The trial bundle helper re-orders and re-numbers those pages among other pages, leaves cited pages out
and re-scans some at lower quality (characters changed as a poor scan would), as the synthetic case
`court_bundle_v2` describes. Every word is made up; nothing here is a real record. Slice B's read log
stands in as `Reads` (as in slices E and F).
"""

from __future__ import annotations

import io
import random
import uuid
from dataclasses import dataclass, field
from datetime import date

import psycopg
from fastapi import FastAPI
from psycopg import AsyncConnection

from app.core.contracts.document_words import word_purpose
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.support.contracts import add_fact
from tests.support.packs import Combo
from tests.support.seed import Seed

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
PARAGRAPH_PURPOSE = "report_paragraph"
VALUE_PURPOSE = "report_citation_value"
QUOTE_PURPOSE = "report_citation_quote"

PAGE_NOS = [*range(101, 131), 214]
CITED = [103, 105, 107, 110, 112, 116, 118, 120, 122, 125, 126, 214]
REMOVED = [125, 126, 214]
RESCANNED = [103, 107, 112, 116, 122]
PLAN = "review x3/52"
SERVED_VIEW = "On balance it is more likely than not that the low iron was not diagnosed in time [p.118]."
CHANGED_VIEW = "The low iron probably would have been diagnosed in time [p.118]."
SERVED_PARAGRAPHS = [
    ("opinion", SERVED_VIEW, [118]),
    ("opinion", "The plan was recorded [p.214] and repeated [p.105].", [214, 105]),
    ("opinion", "Results were seen [p.103] [p.107] [p.110].", [103, 107, 110]),
    ("opinion", "Letters followed [p.112] [p.116] [p.120] [p.122] [p.125] [p.126].", [112, 116, 120, 122, 125, 126]),
]

_VOCAB = (
    "amber basil cedar dune ember fable garnet harbour iris juniper kestrel lantern meadow nectar orchard "
    "pebble quarry raven saffron thistle umber velvet willow yarrow zephyr anchor bramble cobalt dapple "
    "elder fennel glade hazel ivory jasper kelp linden marble nutmeg opal pepper quill russet sorrel "
    "tansy upland vale wren yew zinnia acorn birch clover drift elm flint grove heath inlet jetty knoll"
).split()


def page_words(page_no: int) -> list[str]:
    """The made-up words on a numbered page of the expert's bundle (page 118 carries the plan)."""
    rng = random.Random(page_no)
    words = [rng.choice(_VOCAB) for _ in range(34)] + [f"ref{page_no}"]
    if page_no == 118:
        words[10:10] = PLAN.split()
    return words


def other_words(n: int) -> list[str]:
    """A page of the trial bundle that is not in the expert's bundle."""
    rng = random.Random(10_000 + n)
    return [rng.choice(_VOCAB) for _ in range(34)] + [f"other{n}"]


def rescan(words: list[str]) -> list[str]:
    """The same page scanned again at lower quality: some characters read wrongly."""
    swaps = {"e": "c", "l": "1", "o": "0", "a": "o", "i": "l"}
    out = []
    for n, w in enumerate(words):
        out.append("".join(swaps.get(ch, ch) if (n + i) % 6 == 0 else ch for i, ch in enumerate(w)))
    return out


def pdf_of(pages: list[list[str]], *, size: int = 11) -> bytes:
    """A PDF with a text layer: one page per list of words."""
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    out = io.BytesIO()
    pdf = canvas.Canvas(out, pagesize=A4, invariant=1)
    for words in pages:
        text = pdf.beginText(50, 780)
        text.setFont("Helvetica", size)
        for start in range(0, len(words), 8):
            text.textLine(" ".join(words[start : start + 8]))
        pdf.drawText(text)
        pdf.showPage()
    pdf.save()
    return out.getvalue()


def trial_bundle(
    *, removed: list[int] | None = None, rescanned: list[int] | None = None, extra: int = 9
) -> tuple[bytes, dict[int, int]]:
    """The trial bundle: other pages first, then the expert's pages (less `removed`) in a new order, some
    re-scanned. Returns the PDF and the trial page number of each of the expert's pages kept."""
    removed = REMOVED if removed is None else removed
    rescanned = RESCANNED if rescanned is None else rescanned
    kept = [n for n in PAGE_NOS if n not in removed]
    thirds = [kept[: len(kept) // 3], kept[len(kept) // 3 : 2 * len(kept) // 3], kept[2 * len(kept) // 3 :]]
    order = thirds[2] + thirds[0] + thirds[1]
    pages: list[list[str]] = [other_words(n) for n in range(extra)]
    where: dict[int, int] = {}
    for page_no in order:
        words = page_words(page_no)
        pages.append(rescan(words) if page_no in rescanned else words)
        where[page_no] = len(pages)
    return pdf_of(pages), where


def agenda_pdf(questions: list[str]) -> bytes:
    """An agenda: one numbered question per line."""
    return pdf_of([[w for n, q in enumerate(questions, start=1) for w in f"{n}. {q}".split()]], size=12)


@dataclass
class Reads:
    """In-memory read log: `(case_id, page_id, page_no, person_id)` rows standing in for slice B."""

    rows: set[tuple[uuid.UUID, uuid.UUID, int, uuid.UUID]] = field(default_factory=set)

    async def has_read(self, conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
        return any(r[0] == case_id and r[2] == page_no and r[3] == person_id for r in self.rows)

    async def read_page_ids(self, conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
        return {r[1] for r in self.rows if r[0] == case_id and r[3] == person_id}


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
    document: uuid.UUID
    pages: dict[int, uuid.UUID]
    plan: uuid.UUID
    report: uuid.UUID | None = None
    paragraphs: list[uuid.UUID] = field(default_factory=list)

    def url(self, tail: str) -> str:
        return f"/v1/cases/{self.case}/{tail}"


async def build(
    seed: Seed, admin: psycopg.Connection, aconn: AsyncConnection, combo: Combo, reads: Reads, *, words: bool = True
) -> World:
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
    document = seed.document(case)
    pages = {n: seed.page(case, document, n) for n in PAGE_NOS}
    for n, page in pages.items():
        reads.rows.add((case, page, n, expert))
        if words:
            await seal_words(aconn, case, page, page_words(n))
    plan = add_fact(admin, case, pages[118], status="verified", value=PLAN)
    admin.execute("update app.facts set event_date = '2024-03-12' where id = %s", (plan,))
    return World(
        combo=combo,
        org=org,
        case=case,
        expert=expert,
        co_expert=co_expert,
        assistant=assistant,
        viewer=viewer,
        outsider=outsider,
        document=document,
        pages=pages,
        plan=plan,
    )


async def seal_words(aconn: AsyncConnection, case: uuid.UUID, page: uuid.UUID, words: list[str]) -> None:
    for n, text in enumerate(words, start=1):
        sealed = await CRYPTO.encrypt(aconn, case, text.encode(), purpose=word_purpose(page, f"w{n}"))
        await aconn.execute(
            "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
            " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, %s, 0.99, 'ocr')",
            (page, case, n, f"w{n}", sealed, [0, 0, 1, 0, 1, 1, 0, 1]),
        )


async def serve_report(
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    w: World,
    paragraphs: list[tuple[str, str, list[int]]] | None = None,
    *,
    version_no: int = 1,
) -> uuid.UUID:
    """A served report as slice F leaves it: paragraphs sealed, a citation per cited page."""
    version = uuid.uuid4()
    admin.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name)"
        " values (%s, %s, %s, 'main', 'Report 1')",
        (version, w.case, version_no),
    )
    ids = []
    for para_no, (section, text, cited) in enumerate(paragraphs or SERVED_PARAGRAPHS, start=1):
        sealed = await CRYPTO.encrypt(aconn, w.case, text.encode(), purpose=PARAGRAPH_PURPOSE)
        row = admin.execute(
            "insert into app.report_paragraphs (case_id, report_version_id, para_no, section, content_control_id,"
            " text) values (%s, %s, %s, %s, %s, %s) returning id",
            (w.case, version, para_no, section, f"section:{section}", sealed),
        ).fetchone()
        assert row is not None
        ids.append(row[0])
        for cite_no, page_no in enumerate(cited, start=1):
            fact = w.plan if page_no == 118 else add_fact(admin, w.case, w.pages[page_no], status="verified")
            value = await CRYPTO.encrypt(aconn, w.case, b"zz", purpose=VALUE_PURPOSE)
            quote = await CRYPTO.encrypt(aconn, w.case, b"zz", purpose=QUOTE_PURPOSE)
            admin.execute(
                "insert into app.citations (case_id, report_version_id, paragraph_id, cite_no, fact_id, fact_version,"
                " value_snapshot, quote_verbatim, page_id) values (%s, %s, %s, %s, %s, 2, %s, %s, %s)",
                (w.case, version, row[0], cite_no, fact, value, quote, w.pages[page_no]),
            )
    admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("a" * 64, version))
    admin.execute("update app.report_versions set status = 'served' where id = %s", (version,))
    admin.execute(
        "insert into app.report_servings (case_id, report_version_id, served_on, sent_via)"
        " values (%s, %s, '2026-06-01', 'email')",
        (w.case, version),
    )
    w.report, w.paragraphs = version, ids
    return version


def app_for(
    api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: CaseFileStorage
) -> FastAPI:
    """The API with storage, case encryption, the real pack library and the stand-in read log."""
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=api_db,
        region=str(combo.raw("jurisdiction", "pack.yaml")["cell"]),
        auth=SupabaseAuth(auth_settings(auth_env)),
        mailer=OutboxMailer(),
        crypto=CRYPTO,
        storage=storage,
        packs=default_library(),
        page_reads=reads,
    )


def meeting_type(combo: Combo) -> dict[str, object]:
    """The first meeting type in the case's packs."""
    types = default_library().combine(*combo.codes).meeting_types()
    assert types, "the packs list a meeting type"
    return types[0]


DISCUSSION = date(2026, 11, 2)
