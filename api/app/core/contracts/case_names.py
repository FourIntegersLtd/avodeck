"""The case's party names, opened from the seal, for `pii.strip` before a model call (rule 7).

Slice C owns `case_parties` and seals each name with the case key under `PARTY_NAME_PURPOSE`. The
names are identifiers: never log them (rule 6). Runs on the caller's connection, so row-level security
applies.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto

PARTY_NAME_PURPOSE = "party_name"


async def party_names(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, *, with_parts: bool = False
) -> list[str]:
    """Each party's full name in the order added; `with_parts` adds each part of three letters or more
    after its name (for words that reach the model one per line)."""
    cur = await conn.execute("select name from app.case_parties where case_id = %s order by created_at, id", (case_id,))
    names: list[str] = []
    for (sealed,) in await cur.fetchall():
        full = (await crypto.decrypt(conn, case_id, bytes(sealed), purpose=PARTY_NAME_PURPOSE)).decode()
        names.append(full)
        if with_parts:
            names += [part for part in full.split() if len(part) > 2]
    return names
