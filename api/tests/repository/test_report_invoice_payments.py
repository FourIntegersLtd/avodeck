"""Slice F, invoice payments (review finding 4): each payment is its own row that never changes, an
invoice's paid amount is the sum of its payments, never more than its total less its credit notes, and its
status follows the sums; a new invoice keeps a snapshot of its issuer and tax rate (finding 5).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

INVOICE = (
    "insert into app.invoices (case_id, billing_profile_id, lines, total, tax, currency)"
    " values (%s, %s, '[]', 100.00, 0, 'XTS') returning id"
)
PAY = "insert into app.invoice_payments (invoice_id, amount) values (%s, %s) returning id"
CREDIT = "insert into app.credit_notes (case_id, invoice_id, amount, reason) values (%s, %s, %s, 'zz_reason')"


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, assistant, viewer, outsider = (seed.person() for _ in range(4))
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant", viewer: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    row = admin.execute(
        "insert into app.billing_profiles (owner_type, owner_id, legal_name, address, currency, tax_rate)"
        " values ('organisation', %s, 'Zz Team Ltd', '1 Zz Road', 'XTS', 20) returning id",
        (org,),
    ).fetchone()
    assert row is not None
    return {
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "outsider": outsider,
        "case": case,
        "profile": row[0],
    }


@contextmanager
def refused() -> Iterator[None]:
    with pytest.raises(psycopg.Error) as caught:
        yield
    assert caught.value.sqlstate == "AV409", caught.value.sqlstate


def invoice(admin: psycopg.Connection, world: dict[str, uuid.UUID], status: str = "sent") -> uuid.UUID:
    row = admin.execute(INVOICE, (world["case"], world["profile"])).fetchone()
    assert row is not None
    if status != "draft":
        admin.execute("update app.invoices set status = 'sent' where id = %s", (row[0],))
    if status == "written_off":
        admin.execute("update app.invoices set status = 'written_off' where id = %s", (row[0],))
    found: uuid.UUID = row[0]
    return found


def state(admin: psycopg.Connection, inv: uuid.UUID) -> tuple[str, str]:
    row = admin.execute("select status, paid_amount from app.invoices where id = %s", (inv,)).fetchone()
    assert row is not None
    return str(row[0]), str(row[1])


async def run_as(
    api_db: Database, person: uuid.UUID, sql: str, values: Mapping[str, Any] | tuple[Any, ...] | None = None
) -> list[tuple[object, ...]]:
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, values)
        return await cur.fetchall() if cur.description else []


async def test_payments_are_only_ever_added(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select r, p, has_table_privilege(r, 'app.invoice_payments', p)"
        " from unnest(array['app_api', 'app_worker']) r, unnest(array['select', 'insert', 'update', 'delete']) p"
        " order by r, p"
    ).fetchall()
    allowed = {(r, p) for r, p, ok in rows if ok}
    assert allowed == {("app_api", "select"), ("app_api", "insert"), ("app_worker", "select")}
    rls = admin.execute("select relrowsecurity from pg_class where oid = 'app.invoice_payments'::regclass")
    assert rls.fetchone() == (True,)


async def test_a_payment_is_more_than_nothing(admin: psycopg.Connection, world: dict[str, uuid.UUID]) -> None:
    inv = invoice(admin, world)
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(PAY, (inv, 0))


async def test_the_paid_amount_is_the_sum_of_payments_and_the_status_follows(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    inv = invoice(admin, world)
    admin.execute(PAY, (inv, 40))
    assert state(admin, inv) == ("part_paid", "40.00")
    admin.execute(PAY, (inv, 60))
    assert state(admin, inv) == ("paid", "100.00")


async def test_payments_never_exceed_the_total_less_credit_notes(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    inv = invoice(admin, world)
    admin.execute(CREDIT, (world["case"], inv, 10))
    with refused():
        admin.execute(PAY, (inv, 90.01))
    admin.execute(PAY, (inv, 90))
    assert state(admin, inv) == ("paid", "90.00")


async def test_a_credit_note_cannot_take_what_is_owed_below_what_was_paid(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    inv = invoice(admin, world)
    admin.execute(PAY, (inv, 95))
    with refused():
        admin.execute(CREDIT, (world["case"], inv, 10))
    admin.execute(CREDIT, (world["case"], inv, 5))
    assert state(admin, inv) == ("paid", "95.00"), "the credit settles what is left"


async def test_the_paid_amount_moves_only_with_payments(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    inv = invoice(admin, world)
    with refused():
        await run_as(
            api_db,
            world["expert"],
            "update app.invoices set paid_amount = 50, status = 'part_paid' where id = %s",
            (inv,),
        )
    assert state(admin, inv) == ("sent", "0.00")


@pytest.mark.parametrize("status", ["draft", "written_off", "paid"])
async def test_only_a_sent_or_part_paid_invoice_takes_payments(
    admin: psycopg.Connection, world: dict[str, uuid.UUID], status: str
) -> None:
    inv = invoice(admin, world, "sent" if status == "paid" else status)
    if status == "paid":
        admin.execute(PAY, (inv, 100))
    with refused():
        admin.execute(PAY, (inv, 1))


async def test_who_sees_and_records_payments(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    inv = invoice(admin, world)
    assert len(await run_as(api_db, world["assistant"], PAY, (inv, 10))) == 1
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["viewer"], PAY, (inv, 10))
    see = "select amount from app.invoice_payments where invoice_id = %s"
    assert [str(r[0]) for r in await run_as(api_db, world["expert"], see, (inv,))] == ["10.00"]
    assert await run_as(api_db, world["viewer"], see, (inv,)) == []
    assert await run_as(api_db, world["outsider"], see, (inv,)) == []
    assert state(admin, inv) == ("part_paid", "10.00")


async def test_payments_go_with_their_invoice(admin: psycopg.Connection, world: dict[str, uuid.UUID]) -> None:
    inv = invoice(admin, world)
    admin.execute(PAY, (inv, 10))
    admin.execute("delete from app.invoices where id = %s", (inv,))
    assert admin.execute("select count(*) from app.invoice_payments where invoice_id = %s", (inv,)).fetchone() == (0,)


async def test_a_new_invoice_keeps_its_issuer_and_tax_rate(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    inv = invoice(admin, world)
    admin.execute(
        "update app.billing_profiles set tax_rate = 0, legal_name = 'Zz Renamed Ltd' where id = %s", (world["profile"],)
    )
    row = admin.execute("select tax_rate, issuer from app.invoices where id = %s", (inv,)).fetchone()
    assert row is not None
    assert str(row[0]) == "20.00"
    assert row[1] == {"legal_name": "Zz Team Ltd", "address": "1 Zz Road", "tax_number": None}
