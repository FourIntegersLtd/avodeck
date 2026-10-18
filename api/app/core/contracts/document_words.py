"""Words of a document, opened from the seal (slice A owns `ocr_words`; slices D and E read them here).

Each word is sealed with the case key under `word_purpose(page_id, word_id)`. The opened text is
patient text: never log it (rule 6) and strip identifiers before any model call (rule 7). Runs on the
caller's connection, so row-level security applies.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto


def word_purpose(page_id: uuid.UUID, word_id: str) -> str:
    """The purpose each word in `ocr_words` is sealed under (the same as slice A's)."""
    return f"ocr_words/{page_id}/{word_id}"


async def document_words(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, document_id: uuid.UUID
) -> list[tuple[str, str]]:
    """(`<page_id>/<word_id>`, text) of each of the document's words, in page then reading order."""
    cur = await conn.execute(
        "select w.page_id, w.word_id, w.text from app.ocr_words w join app.pages p on p.id = w.page_id"
        " where p.case_id = %s and p.document_id = %s order by p.bundle_page_no, w.n",
        (case_id, document_id),
    )
    return [
        (
            f"{page}/{word}",
            (await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page, word))).decode(),
        )
        for page, word, sealed in await cur.fetchall()
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
    for fact_id, page, word, sealed in await cur.fetchall():
        text = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page, word))
        out.setdefault(fact_id, []).append((word, text.decode()))
    return out
