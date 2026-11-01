"""Words of a document, opened from the seal (slice A owns `ocr_words`; slices D and E read them here).

Each word is sealed with the case key under `word_purpose(page_id, word_id)`. The opened text is
patient text: never log it (rule 6) and strip identifiers before any model call (rule 7). Runs on the
caller's connection, so row-level security applies.

Where the named expert corrected the computer's reading of some words (slice B, `word_corrections`,
decision 0018), the words read as the expert's reading: one word of the corrected run (its anchor)
carries the expert's text and the rest of the run is left out. `ocr_words` itself is never changed.

Where the reading lands (`apply_corrections`): the anchor is the run's real first word on the page. When
the caller passes only some of the page's words (`cited_words` passes a fact's cited words) and the
run's real first word is not among them, the anchor is the first passed word of the run. A caller holding
the whole page but asking what one fact's words say passes `cited=` and gets the same anchor, so the
reader and `cited_words` agree on what a fact's words say.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto


def word_purpose(page_id: uuid.UUID, word_id: str) -> str:
    """The purpose each word in `ocr_words` is sealed under (the same as slice A's)."""
    return f"ocr_words/{page_id}/{word_id}"


def page_pdf_path(case_id: uuid.UUID, page_id: uuid.UUID) -> str:
    """Where a page's one-page PDF is kept (the same as slice A's)."""
    return f"cases/{case_id}/pages/{page_id}/page.pdf"


type Correction = tuple[list[str], str]  # (word ids of the corrected run, the expert's reading)


def correction_purpose(page_id: uuid.UUID, correction_id: uuid.UUID) -> str:
    """The purpose each expert reading in `word_corrections` is sealed under."""
    return f"word_corrections/{page_id}/{correction_id}"


async def current_corrections(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[Correction]]:
    """The expert's current readings (not replaced by a later one) on each page, oldest first, unsealed."""
    cur = await conn.execute(
        "select id, page_id, word_ids, text from app.word_corrections"
        " where page_id = any (%s) and superseded_at is null order by created_at, id",
        (list(page_ids),),
    )
    out: dict[uuid.UUID, list[Correction]] = {}
    for cid, page, word_ids, sealed in await cur.fetchall():
        text = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=correction_purpose(page, cid))
        out.setdefault(page, []).append((list(word_ids), text.decode()))
    return out


def apply_corrections(
    words: Mapping[str, str], corrections: Iterable[Correction], *, cited: Iterable[str] | None = None
) -> dict[str, str]:
    """`words` (word id -> text, in reading order) with each correction in place: the run's anchor gets the
    expert's reading, the rest of its words present are kept with empty text.

    The anchor is the run's real first word when it is in `words` (and, with `cited`, among the cited
    words); otherwise the first word of the run in `words` that is cited (or simply present). Runs are
    one unbroken stretch of the page, so in reading order their first present word is their real first."""
    out = dict(words)
    order = {w: i for i, w in enumerate(words)}
    chosen = None if cited is None else set(cited)
    for word_ids, text in corrections:
        present = sorted((w for w in set(word_ids) if w in order), key=order.__getitem__)
        if not present:
            continue
        anchor = present[0]
        if chosen is not None and anchor not in chosen:
            anchor = next((w for w in present if w in chosen), anchor)
        for w in present:
            out[w] = text if w == anchor else ""
    return out


def _read_as_corrected(words: list[tuple[str, str]], corrections: Iterable[Correction]) -> list[tuple[str, str]]:
    corrections = list(corrections)
    if not corrections:
        return words
    applied = apply_corrections(dict(words), corrections)
    folded = {w for ids, _ in corrections for w in ids}
    return [(w, applied[w]) for w, _ in words if not (w in folded and applied[w] == "")]


def box(
    word_ids: Sequence[str], polygons: Mapping[str, Sequence[float]], width: float, height: float
) -> dict[str, float]:
    """The words' box as a share of the page (0 to 1), from their polygons and the page size."""
    xs = [x for w in word_ids for x in polygons[w][0::2]]
    ys = [y for w in word_ids for y in polygons[w][1::2]]

    def share(value: float, whole: float) -> float:
        return round(min(1.0, max(0.0, value / whole)), 5)

    return {
        "x0": share(min(xs), width),
        "y0": share(min(ys), height),
        "x1": share(max(xs), width),
        "y1": share(max(ys), height),
    }


async def document_words(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, document_id: uuid.UUID
) -> list[tuple[str, str]]:
    """(`<page_id>/<word_id>`, text) of each of the document's words, in page then reading order."""
    cur = await conn.execute(
        "select w.page_id, w.word_id, w.text from app.ocr_words w join app.pages p on p.id = w.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s) and p.document_id = %s"
        " order by p.bundle_page_no, w.n",
        (case_id, document_id),
    )
    by_page: dict[uuid.UUID, list[tuple[str, str]]] = {}
    for page, word, sealed in await cur.fetchall():
        text = (await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page, word))).decode()
        by_page.setdefault(page, []).append((word, text))
    corrections = await current_corrections(conn, crypto, case_id, by_page)
    return [
        (f"{page}/{word}", text)
        for page, words in by_page.items()
        for word, text in _read_as_corrected(words, corrections.get(page, []))
    ]


async def cited_words(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, fact_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, list[tuple[str, str]]]:
    """(word id, text) of the words each fact cites, in reading order."""
    cur = await conn.execute(
        "select f.id, w.page_id, w.word_id, w.text from app.facts f"
        " join app.ocr_words w on w.page_id = f.page_id and w.word_id = any (f.word_ids)"
        " where f.case_id = %s and f.id = any (%s) order by f.id, w.n",
        (case_id, list(fact_ids)),
    )
    out: dict[uuid.UUID, list[tuple[str, str]]] = {}
    pages: dict[uuid.UUID, uuid.UUID] = {}
    for fact_id, page, word, sealed in await cur.fetchall():
        text = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page, word))
        out.setdefault(fact_id, []).append((word, text.decode()))
        pages[fact_id] = page
    corrections = await current_corrections(conn, crypto, case_id, set(pages.values()))
    return {f: _read_as_corrected(words, corrections.get(pages[f], [])) for f, words in out.items()}
