"""A case with pages, sealed OCR words and record entries, for the records transcript tests (ADR 0028).

Arranged as the local admin role, like `tests/support/seed.py`. Every value is synthetic. Each entry is one or
more printed lines; every word is stored sealed under the case key (the way slice A stores them), one line below
the last, so the reader's line breaks come from the boxes.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date

import psycopg

from app.core.crypto import CaseCrypto
from app.features.pipeline.paths import word_purpose

LINE_HEIGHT = 40.0


@dataclass(frozen=True)
class Spec:
    kind: str
    lines: tuple[str, ...]
    day: date | None = None
    role: str | None = None
    specialty: str | None = None


@dataclass
class Built:
    page_id: uuid.UUID
    entry_ids: list[uuid.UUID] = field(default_factory=list)
    word_ids: list[str] = field(default_factory=list)


async def add_document(aconn: psycopg.AsyncConnection, case: uuid.UUID) -> uuid.UUID:
    doc = uuid.uuid4()
    await aconn.execute(
        "insert into app.documents (id, case_id, kind, filename, sha256) values (%s, %s, 'records', 'f.pdf', %s)",
        (doc, case, "0" * 64),
    )
    return doc


async def add_page(
    aconn: psycopg.AsyncConnection,
    crypto: CaseCrypto,
    case: uuid.UUID,
    document: uuid.UUID,
    number: int,
    specs: list[Spec],
    *,
    unreadable: bool = False,
    duplicate_of: uuid.UUID | None = None,
    superseded_by: uuid.UUID | None = None,
    quarantined: bool = False,
    analysed: bool = True,
    failed: bool = False,
) -> Built:
    page = uuid.uuid4()
    await aconn.execute(
        "insert into app.pages (id, case_id, document_id, bundle_page_no, unreadable, duplicate_of_page_id,"
        " superseded_by_page_id, quarantined) values (%s, %s, %s, %s, %s, %s, %s, %s)",
        (page, case, document, number, unreadable, duplicate_of, superseded_by, quarantined),
    )
    if analysed:
        await aconn.execute(
            "insert into app.page_analysis (page_id, case_id, extracted_at, entries_extracted_at, needs_human,"
            " entries_failed_at) values (%s, %s, now(), case when %s then null else now() end, %s,"
            " case when %s then now() end)",
            (page, case, failed, failed, failed),
        )
    built = Built(page)
    n = 0
    row = 0
    for spec in specs:
        first = n + 1
        for line in spec.lines:
            x = 60.0
            for word in line.split():
                n += 1
                wid = f"p{number}w{n}"
                x1 = x + 11.0 * len(word)
                y = 80.0 + row * LINE_HEIGHT
                sealed = await crypto.encrypt(aconn, case, word.encode(), purpose=word_purpose(page, wid))
                await aconn.execute(
                    "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
                    " values (%s, %s, %s, %s, %s, %s::real[], 0.99, 'ocr')",
                    (page, case, n, wid, sealed, [x, y, x1, y, x1, y + 24.0, x, y + 24.0]),
                )
                built.word_ids.append(wid)
                x = x1 + 10.0
            row += 1
        entry = uuid.uuid4()
        await aconn.execute(
            "insert into app.record_entries (id, page_id, case_id, kind, first_n, last_n, author_role, specialty,"
            " event_date, date_basis, extraction_version) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'v1')",
            (
                entry,
                page,
                case,
                spec.kind,
                first,
                n,
                spec.role,
                spec.specialty,
                spec.day,
                "printed" if spec.day else None,
            ),
        )
        built.entry_ids.append(entry)
    return built
