"""Seed `facts` rows for tests of slices that read facts (D Understand, E Write, ...), as the local admin role.

`insert_fact()` writes one fact with the kind, fields, value, words and dates a test needs and walks the
fact state machine to `status` (a new fact starts `suggested`; `verified` and `edited` are one step on).
Values are made up; nothing here is a real record. Kinds and fields come from the caller, so a test
running under the fixture packs passes the fixture's own kinds (rule 11).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from tests.support.contracts import FACT_PATH


def insert_fact(
    admin_conn: psycopg.Connection,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    kind: str,
    fields: Mapping[str, Any] | None = None,
    value: str | None = None,
    word_ids: Sequence[str] = ("w1",),
    event_date: date | None = None,
    status: str = "verified",
    *,
    date_basis: str | None = None,
    available_at: datetime | None = None,
    availability_basis: str | None = None,
) -> uuid.UUID:
    fact_id = uuid.uuid4()
    admin_conn.execute(
        """
        insert into app.facts (id, case_id, page_id, kind, fields, value, word_ids, event_date, date_basis,
                               available_at, availability_basis)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            fact_id,
            case_id,
            page_id,
            kind,
            Jsonb(dict(fields or {})),
            value,
            list(word_ids),
            event_date,
            date_basis,
            available_at,
            availability_basis,
        ),
    )
    for step in FACT_PATH[status]:
        admin_conn.execute("update app.facts set status = %s where id = %s", (step, fact_id))
    return fact_id
