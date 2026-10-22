"""Fill `event_date` on verify when the printed words allow (including two-digit years after Correct)."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Mapping, Sequence
from typing import Any

from psycopg import AsyncConnection

from app.core.contracts.document_words import word_purpose
from app.core.contracts.fact_dates import case_anchor, resolve_fact_event_date
from app.core.crypto import CaseCrypto
from app.core.packs import PackLibrary
from app.features.reader import repo


async def resolve_for_verify(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    packs: PackLibrary,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    kind: str,
    fields: Mapping[str, Any],
    word_ids: Sequence[str],
) -> tuple[dt.date | None, str | None]:
    codes = await repo.case_codes(conn, case_id)
    if codes is None:
        return None, None
    pack = packs.for_case(codes)
    cur = await conn.execute(
        "select word_id, text, struck from app.ocr_words where page_id = %s order by n",
        (page_id,),
    )
    ordered: list[tuple[str, str]] = []
    struck: set[str] = set()
    for word_id, sealed, is_struck in await cur.fetchall():
        text = (await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page_id, word_id))).decode()
        ordered.append((word_id, text))
        if is_struck:
            struck.add(word_id)
    return resolve_fact_event_date(
        kind,
        fields,
        word_ids,
        ordered,
        struck,
        pack,
        allow_century=True,
        anchor=await case_anchor(conn, case_id),
    )
