"""The named expert's report inputs for one case (slice C's profile tables; slice F builds with them).

Slice C keeps the template, declarations and signature private to the expert, and the conflicts
declaration to the API. The foundation functions in migration 0026 open exactly the named expert's
rows for one case: to the case's own job, to an API caller who may build the report (`report.build`),
and the signature only to whoever may sign (`report.sign`). Anyone else gets nothing.
Runs on the caller's connection, so the database checks the caller.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

from psycopg import AsyncConnection


@dataclass(frozen=True)
class ReportTemplate:
    """The named expert's latest Word template (the file is sealed in Storage)."""

    id: uuid.UUID
    version: int
    filename: str
    storage_path: str
    sha256: str
    heading_map: list[Any]
    style_map: list[Any]
    mapping: list[Any]


@dataclass(frozen=True)
class Declaration:
    """Wording the named expert adds to one of the jurisdiction pack's declarations."""

    kind: str
    text: str | None
    locked_at: datetime | None


@dataclass(frozen=True)
class Signature:
    """The named expert's newest signature image (sealed in Storage)."""

    id: uuid.UUID
    method: str
    storage_path: str
    sha256: str
    width: int | None
    height: int | None
    created_at: datetime


@dataclass(frozen=True)
class ConflictAnswer:
    """One answer to a jurisdiction pack conflict question; `detail` stays sealed under the case key."""

    question_key: str
    answer: str
    detail: bytes | None


@dataclass(frozen=True)
class ConflictDeclaration:
    """The case's latest conflicts declaration; `out_of_date` once an answer changed after it."""

    outcome: str
    declared_at: datetime
    out_of_date: bool
    answers: tuple[ConflictAnswer, ...]


async def report_template(conn: AsyncConnection, case_id: uuid.UUID) -> ReportTemplate | None:
    cur = await conn.execute(
        "select id, version, filename, storage_path, sha256, heading_map, style_map, mapping"
        " from app.case_report_template(%s)",
        (case_id,),
    )
    row = await cur.fetchone()
    return ReportTemplate(*row) if row else None


async def declarations(conn: AsyncConnection, case_id: uuid.UUID) -> list[Declaration]:
    """The named expert's rows for the case's jurisdiction, by kind."""
    cur = await conn.execute("select kind, text, locked_at from app.case_declarations(%s)", (case_id,))
    return [Declaration(*row) for row in await cur.fetchall()]


async def signature(conn: AsyncConnection, case_id: uuid.UUID) -> Signature | None:
    cur = await conn.execute(
        "select id, method, storage_path, sha256, width, height, created_at from app.case_signature(%s)",
        (case_id,),
    )
    row = await cur.fetchone()
    return Signature(*row) if row else None


async def conflict_declaration(conn: AsyncConnection, case_id: uuid.UUID) -> ConflictDeclaration | None:
    cur = await conn.execute(
        "select outcome, declared_at, out_of_date from app.case_conflict_declaration(%s)", (case_id,)
    )
    row = await cur.fetchone()
    if row is None:
        return None
    cur = await conn.execute("select question_key, answer, detail from app.case_conflict_answers(%s)", (case_id,))
    answers = tuple(
        ConflictAnswer(key, answer, bytes(detail) if detail is not None else None)
        for key, answer, detail in await cur.fetchall()
    )
    return ConflictDeclaration(row[0], row[1], bool(row[2]), answers)


@dataclass(frozen=True)
class NamedExpert:
    """The case's named expert: name, registration and fee details (the profile row may be missing)."""

    person_id: uuid.UUID
    full_name: str
    registration_body: str | None
    registration_number: str | None
    hourly_rate: Decimal | None
    currency: str | None


async def named_expert(conn: AsyncConnection, case_id: uuid.UUID) -> NamedExpert | None:
    """For the case's own job or a caller who may see the case's content (`case.view_content`); else None.
    Wraps `app.report_named_expert` (migration 0602), which reads `people` and `experts`."""
    cur = await conn.execute(
        "select person_id, full_name, registration_body, registration_number, hourly_rate, currency"
        " from app.report_named_expert(%s)",
        (case_id,),
    )
    row = await cur.fetchone()
    return NamedExpert(*row) if row is not None else None
