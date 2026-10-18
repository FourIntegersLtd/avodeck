"""Sent invoices of a case (slice F's `app.case_invoice_rows`, migration 0603), for slice H's `invoices.csv`.

Every invoice that left Avodeck (never a draft), oldest first, with what was paid and what credit notes
took off. Runs on the caller's connection, so row-level security decides: the case's own job, or a
person allowed to see the case's invoices.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from psycopg import AsyncConnection


@dataclass(frozen=True)
class InvoiceRow:
    invoice_id: uuid.UUID
    number: int
    number_text: str
    status: str
    total: Decimal
    tax: Decimal
    currency: str
    paid_amount: Decimal
    credited: Decimal
    sent_at: datetime


async def case_invoice_rows(conn: AsyncConnection, case_id: uuid.UUID) -> list[InvoiceRow]:
    cur = await conn.execute(
        "select invoice_id, number, number_text, status, total, tax, currency, paid_amount, credited, sent_at"
        " from app.case_invoice_rows(%s)",
        (case_id,),
    )
    return [InvoiceRow(*row) for row in await cur.fetchall()]
