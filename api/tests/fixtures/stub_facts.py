"""Stub fact loader: the planted bundle's expected facts, put into a case without running the pipeline.

Slices that read facts (B, D, E) use it in tests. It reads
`evals/gb-eaw/clin-neg-breach/general-practice/expected/facts.json` and, for each chosen bundle page:

- adds the page (or reuses the case's page with that number),
- writes each fact's quote as `ocr_words` (`source = 'ocr'`, ids `p{page}w{n}`, one line per fact,
  sealed under the case key when a `CaseCrypto` is given, else stored as plain bytes),
- adds one `suggested` fact citing exactly those words, with a `bbox` around them (fractions of the page).

`verified=True` walks every fact to `verified` through the state machine. All values are synthetic.

    loaded = await load_stub_facts(conn, case_id, pages=[118], crypto=crypto, verified=True)
    loaded[118].page_id, loaded[118].fact_ids

`conn` is an admin connection (tests arrange data as the local admin role, like `tests/support/seed.py`).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from app.core.crypto import CaseCrypto
from app.features.pipeline.paths import word_purpose

FACTS_JSON = Path(__file__).resolve().parents[3] / "evals/gb-eaw/clin-neg-breach/general-practice/expected/facts.json"

# A made-up page layout: every fact on its own line.
PAGE_WIDTH, PAGE_HEIGHT = 1000.0, 1400.0
LEFT, TOP, LINE, WORD_HEIGHT, CHAR, GAP = 60.0, 80.0, 50.0, 24.0, 11.0, 10.0


@dataclass(frozen=True)
class StubFact:
    page: int
    kind: str
    value: str
    quote: str
    event_date: str | None
    fields: dict[str, Any]
    planted: str | None


@dataclass
class StubPage:
    page_id: uuid.UUID
    fact_ids: list[uuid.UUID] = field(default_factory=list)


@cache
def _all() -> tuple[StubFact, ...]:
    raw = json.loads(FACTS_JSON.read_text())
    return tuple(
        StubFact(
            page=int(r["page"]),
            kind=r["kind"],
            value=r["value"],
            quote=r["quote"],
            event_date=r.get("event_date"),
            fields=r.get("fields") or {},
            planted=r.get("planted"),
        )
        for r in raw
    )


def expected_facts(pages: Iterable[int] | None = None) -> list[StubFact]:
    """The expected facts, in file order; only those on `pages` when given."""
    if pages is None:
        return list(_all())
    wanted = set(pages)
    return [f for f in _all() if f.page in wanted]


async def _page_id(conn: psycopg.AsyncConnection, case_id: uuid.UUID, number: int) -> uuid.UUID:
    cur = await conn.execute(
        "select id from app.pages"
        " where bundle_id = (select bundle_id from app.cases where id = %s) and bundle_page_no = %s",
        (case_id, number),
    )
    row = await cur.fetchone()
    if row is not None:
        return uuid.UUID(str(row[0]))
    doc, pid = uuid.uuid4(), uuid.uuid4()
    await conn.execute(
        "insert into app.documents (id, bundle_id, kind, filename, sha256)"
        " values (%s, (select bundle_id from app.cases where id = %s), 'records', 'f.pdf', %s)",
        (doc, case_id, "0" * 64),
    )
    await conn.execute(
        "insert into app.pages (id, bundle_id, document_id, bundle_page_no)"
        " values (%s, (select bundle_id from app.cases where id = %s), %s, %s)",
        (pid, case_id, doc, number),
    )
    return pid


async def load_stub_facts(
    conn: psycopg.AsyncConnection,
    case_id: uuid.UUID,
    *,
    pages: Iterable[int] | None = None,
    crypto: CaseCrypto | None = None,
    verified: bool = False,
) -> dict[int, StubPage]:
    """Put the expected facts for `pages` (every page when None) into the case; returns them by page number."""
    by_page: dict[int, list[StubFact]] = {}
    for fact in expected_facts(pages):
        by_page.setdefault(fact.page, []).append(fact)

    loaded: dict[int, StubPage] = {}
    for number, facts in sorted(by_page.items()):
        page = StubPage(await _page_id(conn, case_id, number))
        cur = await conn.execute("select coalesce(max(n), 0) from app.ocr_words where page_id = %s", (page.page_id,))
        row = await cur.fetchone()
        n = int(row[0]) if row else 0
        for line, fact in enumerate(facts):
            y0 = TOP + line * LINE
            x = LEFT
            word_ids: list[str] = []
            for word in fact.quote.split():
                n += 1
                wid = f"p{number}w{n}"
                x1 = x + CHAR * len(word)
                data = word.encode()
                text = (
                    await crypto.encrypt(conn, case_id, data, purpose=word_purpose(page.page_id, wid))
                    if crypto
                    else data
                )
                await conn.execute(
                    "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
                    " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, %s::real[], 0.99, "
                    "'ocr')",
                    (page.page_id, case_id, n, wid, text, [x, y0, x1, y0, x1, y0 + WORD_HEIGHT, x, y0 + WORD_HEIGHT]),
                )
                word_ids.append(wid)
                x = x1 + GAP
            bbox = {
                "x0": LEFT / PAGE_WIDTH,
                "y0": y0 / PAGE_HEIGHT,
                "x1": min(x - GAP, PAGE_WIDTH) / PAGE_WIDTH,
                "y1": (y0 + WORD_HEIGHT) / PAGE_HEIGHT,
            }
            fid = uuid.uuid4()
            await conn.execute(
                "insert into app.facts (id, case_id, page_id, kind, fields, value, word_ids, bbox, event_date)"
                " values (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    fid,
                    case_id,
                    page.page_id,
                    fact.kind,
                    Jsonb(fact.fields),
                    fact.value,
                    word_ids,
                    Jsonb(bbox),
                    date.fromisoformat(fact.event_date) if fact.event_date else None,
                ),
            )
            if verified:
                await conn.execute("update app.facts set status = 'verified' where id = %s", (fid,))
            page.fact_ids.append(fid)
        loaded[number] = page
    return loaded
