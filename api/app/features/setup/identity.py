"""The subject's own name, other names and date of birth, sealed under the case key (rule 7).

Slice C stores them (table `case_subject_identity`, migration 0396); only the named expert writes them
and only the worker and AI paths open them. They are identifiers: never log them (rule 6).
"""

from __future__ import annotations

import json
import uuid
from datetime import date

import dateparser
from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto
from app.core.packs import MergedPack

SUBJECT_NAME = "subject_name"
SUBJECT_OTHER_NAMES = "subject_other_names"
SUBJECT_DOB = "subject_dob"


def read_date_of_birth(text: str, pack: MergedPack) -> date | None:
    """A whole date (day, month and year) written in `text`, read in the pack's date order; else None."""
    got = dateparser.parse(
        text.strip(),
        languages=[pack.language],
        settings={
            "DATE_ORDER": pack.date_order,
            "REQUIRE_PARTS": ["day", "month", "year"],
            "STRICT_PARSING": True,
        },
    )
    return got.date() if got else None


async def save_identity(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    case_id: uuid.UUID,
    *,
    name: str,
    other_names: list[str],
    date_of_birth: date | None,
) -> None:
    sealed_name = await crypto.encrypt(conn, case_id, name.encode(), purpose=SUBJECT_NAME)
    sealed_other = await crypto.encrypt(conn, case_id, json.dumps(other_names).encode(), purpose=SUBJECT_OTHER_NAMES)
    sealed_dob = (
        await crypto.encrypt(conn, case_id, date_of_birth.isoformat().encode(), purpose=SUBJECT_DOB)
        if date_of_birth
        else None
    )
    await conn.execute(
        "insert into app.case_subject_identity (case_id, name, other_names, date_of_birth)"
        " values (%s, %s, %s, %s) on conflict (case_id) do update set name = excluded.name,"
        " other_names = excluded.other_names, date_of_birth = excluded.date_of_birth",
        (case_id, sealed_name, sealed_other, sealed_dob),
    )
