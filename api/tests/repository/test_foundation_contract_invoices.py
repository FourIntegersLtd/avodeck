"""Sent invoices of a case for slice H's export (`app.core.contracts.invoices`; request from slice F).

`case_invoice_rows(conn, case_id)` wraps slice F's `app.case_invoice_rows`: every sent invoice of the case
(never a draft) with its number, status, amounts, currency, what was paid and what credit notes took off.
Row-level security still applies: the case's own job sees them, a job on another case sees nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from decimal import Decimal

import psycopg
import pytest

from app.core.contracts.invoices import InvoiceRow, case_invoice_rows
from app.core.db import Database
from tests.support.contracts import add_sent_invoice
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, other = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="served")
    elsewhere = seed.case(seed.org(kind="personal", members={other: "owner"}), other, status="served")
    invoice = add_sent_invoice(admin, case, expert, credited="4.00")
    return {"case": case, "elsewhere": elsewhere, "invoice": invoice}


async def test_the_cases_job_reads_its_sent_invoices_with_credits(
    worker_db: Database, world: dict[str, uuid.UUID]
) -> None:
    async with worker_db.job(world["case"]) as conn:
        rows = await case_invoice_rows(conn, world["case"])
    assert len(rows) == 1, "drafts are not exported"
    row = rows[0]
    assert isinstance(row, InvoiceRow)
    assert (row.invoice_id, row.number_text, row.status, row.currency) == (world["invoice"], "ZZ-1", "sent", "XTS")
    assert (row.number, row.total, row.tax, row.paid_amount, row.credited) == (
        1,
        Decimal("120.00"),
        Decimal("20.00"),
        Decimal("0.00"),
        Decimal("4.00"),
    )
    assert row.sent_at is not None and row.sent_at.tzinfo is not None


async def test_a_job_on_another_case_reads_nothing(worker_db: Database, world: dict[str, uuid.UUID]) -> None:
    async with worker_db.job(world["elsewhere"]) as conn:
        assert await case_invoice_rows(conn, world["case"]) == []
