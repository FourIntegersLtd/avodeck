"""Every identifier of a case that must be stripped before a model call (rule 7), opened from the seal.

One answer for every slice: the parties' names and their parts (slice C's `case_parties`) merged with
the subject's own name, its parts, other names and date of birth (slice C's `case_subject_identity`,
ADR 0026). Pass `names` and `dates_of_birth` to `pii.strip` or `AiKit.run`. The values are identifiers:
never log them (rule 6). Runs on the caller's connection, so row-level security applies: a caller who may
not see the case gets no names.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date

from psycopg import AsyncConnection

from app.core.contracts.case_names import party_names
from app.core.crypto import CaseCrypto

SUBJECT_NAME = "subject_name"
SUBJECT_OTHER_NAMES = "subject_other_names"
SUBJECT_DOB = "subject_dob"
MIN_PART = 3


@dataclass(frozen=True)
class Identity:
    names: Sequence[str] = ()
    dates_of_birth: Sequence[date] = ()


def _with_parts(full: str) -> list[str]:
    return [full] + [part for part in full.split() if len(part) >= MIN_PART]


async def identity_for(conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID) -> Identity:
    """The case's names (parties, then the subject and any other names, each followed by its parts) and
    the subject's date of birth. A case with no recorded identity gets the parties only."""
    names = await party_names(conn, crypto, case_id, with_parts=True)
    cur = await conn.execute(
        "select name, other_names, date_of_birth from app.case_subject_identity where case_id = %s", (case_id,)
    )
    row = await cur.fetchone()
    if row is None:
        return Identity(names=names)
    sealed_name, sealed_others, sealed_dob = row
    names += _with_parts((await crypto.decrypt(conn, case_id, bytes(sealed_name), purpose=SUBJECT_NAME)).decode())
    if sealed_others is not None:
        others = json.loads(await crypto.decrypt(conn, case_id, bytes(sealed_others), purpose=SUBJECT_OTHER_NAMES))
        for other in others:
            names += _with_parts(str(other))
    born: list[date] = []
    if sealed_dob is not None:
        raw = await crypto.decrypt(conn, case_id, bytes(sealed_dob), purpose=SUBJECT_DOB)
        born.append(date.fromisoformat(raw.decode()))
    return Identity(names=list(dict.fromkeys(names)), dates_of_birth=born)
