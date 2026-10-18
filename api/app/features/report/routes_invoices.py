"""Screen 18b: billing profile, invoices, payments and credit notes (architecture 10.8).

- `GET/PUT /me/billing-profile`: the expert's own issuer details for personal cases (a team's profile is
  slice O's). Changing the tax number needs step-up.
- `POST /cases/{id}/invoices` (`invoice.create`): brings the case's open draft up to date, or starts one,
  from the named expert's time (`routes_time.totals`) at the issuer's hourly rate (the expert profile's rate
  when the issuer has none), the issuer's tax rate and currency, plus expenses. The issuer is the
  organisation for a team case and the expert for a personal case (`app.case_billing_profile`); 409
  `no_billing_profile` / `no_hourly_rate` otherwise.
- `POST .../invoices/{inv}/send` (`invoice.send`, step-up): numbers the invoice from its issuer (gap-free, by
  the database), stores its PDF sealed under the case key and mails the recipient the case's id, the number
  and the amount only (`mail.py`). A sent invoice never changes.
- `PATCH .../invoices/{inv}/payment` (`invoice.record_payment`; writing off also needs `credit_note.issue`)
  and `POST .../invoices/{inv}/credit-notes` (`credit_note.issue`, step-up) record payment and corrections.
- `GET .../invoices` and `GET .../invoices/{inv}/pdf` (a draft's PDF is drawn on request, never stored).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import Depends, Request, Response
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.auth.session import current_session
from app.core.auth.step_up import check_step_up
from app.core.contracts.instructing_parties import instructing_party_contacts
from app.core.email import Mail
from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.packs import MergedPack
from app.core.storage import case_prefix
from app.features.report.common import Crypto, Pack, Storage, invalid, not_found
from app.features.report.invoice_pdf import InvoiceDoc, render_invoice
from app.features.report.mail import INVOICE_SENT
from app.features.report.router import router
from app.features.report.routes_time import totals
from app.features.report.services import current

CreateInvoice = Annotated[CaseAccess, Depends(require_case_access("invoice.create"))]
SendInvoice = Annotated[CaseAccess, Depends(require_case_access("invoice.send"))]
RecordPayment = Annotated[CaseAccess, Depends(require_case_access("invoice.record_payment"))]
IssueCredit = Annotated[CaseAccess, Depends(require_case_access("credit_note.issue"))]

CENT = Decimal("0.01")
EMAIL = r"^[^@\s]+@[^@\s]+$"
KEY = r"^[a-z][a-z0-9_]{0,39}$"
Money = Annotated[Decimal, Field(ge=0, max_digits=12, decimal_places=2)]


def cents(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


# Billing profile ------------------------------------------------------------------------------------------


class BillingProfileIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    legal_name: str = Field(min_length=1, max_length=200)
    address: str | None = Field(default=None, max_length=1000)
    tax_number: str | None = Field(default=None, max_length=60)
    tax_rate: Annotated[Decimal, Field(ge=0, le=100, max_digits=5, decimal_places=2)] = Decimal(0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    hourly_rate: Money | None = None
    invoice_prefix: str = Field(default="", max_length=20)


class BillingProfile(BillingProfileIn):
    id: uuid.UUID


class BillingProfileOut(BaseModel):
    profile: BillingProfile | None


_PROFILE_COLS = "id, legal_name, address, tax_number, tax_rate, currency, hourly_rate, invoice_prefix, version"


async def _own_profile(conn: Any) -> dict[str, Any] | None:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_PROFILE_COLS} from app.billing_profiles"
            " where owner_type = 'person' and owner_id = app.current_person_id()"
        )
        row: dict[str, Any] | None = await cur.fetchone()
        return row


def _profile_out(row: dict[str, Any] | None, response: Response) -> BillingProfileOut:
    response.headers["etag"] = etag(int(row.pop("version")) if row else 0)
    return BillingProfileOut(profile=BillingProfile(**row) if row else None)


@router.get("/me/billing-profile", response_model=BillingProfileOut)
async def get_billing_profile(conn: DbConn, response: Response) -> BillingProfileOut:
    return _profile_out(await _own_profile(conn), response)


@router.put("/me/billing-profile", response_model=BillingProfileOut)
async def put_billing_profile(
    body: BillingProfileIn, if_match: IfMatch, request: Request, conn: DbConn, response: Response
) -> BillingProfileOut:
    row = await _own_profile(conn)
    if (int(row["version"]) if row else 0) != if_match:
        raise stale()
    if (row["tax_number"] if row else None) != body.tax_number:
        check_step_up(current_session(request))
    values = body.model_dump()
    if row is None:
        cur = await conn.execute(
            "insert into app.billing_profiles (owner_type, owner_id, legal_name, address, tax_number, tax_rate,"
            " currency, hourly_rate, invoice_prefix) values ('person', app.current_person_id(), %(legal_name)s,"
            " %(address)s, %(tax_number)s, %(tax_rate)s, %(currency)s, %(hourly_rate)s, %(invoice_prefix)s)"
            " returning id",
            values,
        )
    else:
        cur = await conn.execute(
            "update app.billing_profiles set legal_name = %(legal_name)s, address = %(address)s,"
            " tax_number = %(tax_number)s, tax_rate = %(tax_rate)s, currency = %(currency)s,"
            " hourly_rate = %(hourly_rate)s, invoice_prefix = %(invoice_prefix)s, version = version + 1,"
            " updated_at = now() where id = %(id)s returning id",
            {**values, "id": row["id"]},
        )
    saved = await cur.fetchone()
    assert saved is not None
    await audit.record(conn, action="billing_profile.changed", target=f"billing_profile:{saved[0]}")
    return _profile_out(await _own_profile(conn), response)


# Invoices -------------------------------------------------------------------------------------------------


class InvoiceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expenses: Money = Decimal(0)


class SendIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    to: str | None = Field(default=None, pattern=EMAIL, max_length=254)


class PaymentIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["part_paid", "paid", "written_off"]
    paid_amount: Money | None = None


class CreditIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: Annotated[Decimal, Field(gt=0, max_digits=12, decimal_places=2)]
    reason: str = Field(pattern=KEY)


class CreditNoteOut(BaseModel):
    id: uuid.UUID
    number_text: str | None
    amount: Decimal
    reason: str
    issued_at: datetime


class InvoiceOut(BaseModel):
    id: uuid.UUID
    number_text: str | None
    status: str
    lines: list[dict[str, Any]]
    tax: Decimal
    total: Decimal
    currency: str
    paid_amount: Decimal
    sent_to: str | None
    sent_at: datetime | None
    created_at: datetime
    credit_notes: list[CreditNoteOut] = []


class InvoicesOut(BaseModel):
    invoices: list[InvoiceOut]


_INVOICE_COLS = "id, number_text, status, lines, tax, total, currency, paid_amount, sent_to, sent_at, created_at"


async def _invoices(conn: Any, case_id: uuid.UUID, invoice_id: uuid.UUID | None = None) -> list[InvoiceOut]:
    sql = f"select {_INVOICE_COLS} from app.invoices where case_id = %s"
    args: list[Any] = [case_id]
    if invoice_id is not None:
        sql += " and id = %s"
        args.append(invoice_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(sql + " order by created_at", args)
        rows = await cur.fetchall()
        await cur.execute(
            "select invoice_id, id, number_text, amount, reason, issued_at from app.credit_notes"
            " where case_id = %s order by number",
            (case_id,),
        )
        credits: dict[uuid.UUID, list[CreditNoteOut]] = {}
        for c in await cur.fetchall():
            credits.setdefault(c.pop("invoice_id"), []).append(CreditNoteOut(**c))
    return [InvoiceOut(**r, credit_notes=credits.get(r["id"], [])) for r in rows]


async def _one(conn: Any, case_id: uuid.UUID, invoice_id: uuid.UUID) -> InvoiceOut:
    found = await _invoices(conn, case_id, invoice_id)
    if not found:
        raise not_found()
    return found[0]


async def _issuer(conn: Any, case_id: uuid.UUID) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute("select * from app.case_billing_profile(%s)", (case_id,))
        row: dict[str, Any] | None = await cur.fetchone()
    if row is None:
        raise ApiError("no_billing_profile", "Add who the invoice is from first.", 409)
    return row


async def _hourly_rate(conn: Any, case_id: uuid.UUID, issuer: dict[str, Any]) -> Decimal:
    if issuer["hourly_rate"] is not None:
        return Decimal(issuer["hourly_rate"])
    cur = await conn.execute("select hourly_rate, currency from app.report_named_expert(%s)", (case_id,))
    row = await cur.fetchone()
    if row is not None and row[0] is not None and row[1] == issuer["currency"]:
        return Decimal(row[0])
    raise ApiError("no_hourly_rate", "Add your hourly rate first.", 409)


@router.get("/cases/{case_id}/invoices", response_model=InvoicesOut)
async def list_invoices(case_id: uuid.UUID, access: RecordPayment, conn: DbConn) -> InvoicesOut:
    return InvoicesOut(invoices=await _invoices(conn, case_id))


@router.post("/cases/{case_id}/invoices", response_model=InvoiceOut, status_code=201)
async def draft_invoice(
    case_id: uuid.UUID, body: InvoiceIn, access: CreateInvoice, conn: DbConn, pack: Pack
) -> InvoiceOut:
    issuer = await _issuer(conn, case_id)
    rate = await _hourly_rate(conn, case_id, issuer)
    seconds = (await totals(conn, case_id, pack)).total_seconds
    hours = cents(Decimal(seconds) / Decimal(3600))
    time_amount = cents(hours * rate)
    expenses = cents(body.expenses)
    lines = [
        {"kind": "time", "seconds": seconds, "hours": str(hours), "rate": str(cents(rate)), "amount": str(time_amount)},
        {"kind": "expenses", "amount": str(expenses)},
    ]
    tax = cents((time_amount + expenses) * Decimal(issuer["tax_rate"]) / Decimal(100))
    total = time_amount + expenses + tax
    values = (issuer["id"], Jsonb(lines), total, tax, issuer["currency"])
    cur = await conn.execute(
        "select id from app.invoices where case_id = %s and status = 'draft' order by created_at desc limit 1",
        (case_id,),
    )
    draft = await cur.fetchone()
    if draft is None:
        cur = await conn.execute(
            "insert into app.invoices (billing_profile_id, lines, total, tax, currency, case_id)"
            " values (%s, %s, %s, %s, %s, %s) returning id",
            (*values, case_id),
        )
    else:
        cur = await conn.execute(
            "update app.invoices set billing_profile_id = %s, lines = %s, total = %s, tax = %s, currency = %s,"
            " version = version + 1 where id = %s returning id",
            (*values, draft[0]),
        )
    row = await cur.fetchone()
    assert row is not None
    return await _one(conn, case_id, row[0])


async def _document(
    conn: Any, case_id: uuid.UUID, pack: MergedPack, invoice: InvoiceOut, issued_on: date
) -> InvoiceDoc:
    issuer = await _issuer(conn, case_id)
    parties = [p for p in await instructing_party_contacts(conn, case_id)]
    party = parties[0] if parties else None
    return InvoiceDoc(
        language=pack.language,
        date_order=pack.date_order,
        number=invoice.number_text,
        issued_on=issued_on,
        issuer=str(issuer["legal_name"]),
        issuer_address=issuer["address"],
        tax_number=issuer["tax_number"],
        recipient=f"{party.name}, {party.firm}" if party else None,
        reference=(party.reference if party and party.reference else str(case_id)),
        currency=invoice.currency,
        tax_rate=Decimal(issuer["tax_rate"]),
        lines=invoice.lines,
        tax=invoice.tax,
        total=invoice.total,
        credits=[(c.number_text or "", c.amount) for c in invoice.credit_notes],
    )


def invoice_path(case_id: uuid.UUID, invoice_id: uuid.UUID) -> str:
    return f"{case_prefix(case_id)}/invoices/{invoice_id}.pdf"


@router.post("/cases/{case_id}/invoices/{invoice_id}/send", response_model=InvoiceOut)
async def send_invoice(
    case_id: uuid.UUID,
    invoice_id: uuid.UUID,
    body: SendIn,
    access: SendInvoice,
    conn: DbConn,
    pack: Pack,
    crypto: Crypto,
    storage: Storage,
) -> InvoiceOut:
    invoice = await _one(conn, case_id, invoice_id)
    if invoice.status != "draft":
        raise ApiError("already_sent", "This invoice was sent; correct it with a credit note.", 409)
    to = body.to
    if to is None:
        to = next((p.email for p in await instructing_party_contacts(conn, case_id) if p.email), None)
    if to is None:
        raise invalid("to")
    await conn.execute(
        "update app.invoices set status = 'sent', sent_to = %s, version = version + 1 where id = %s",
        (to, invoice_id),
    )
    invoice = await _one(conn, case_id, invoice_id)
    assert invoice.sent_at is not None and invoice.number_text is not None
    issued_on = invoice.sent_at.astimezone(ZoneInfo(pack.timezone)).date()
    pdf = render_invoice(await _document(conn, case_id, pack, invoice, issued_on))
    path = invoice_path(case_id, invoice_id)
    await storage.put(path, await crypto.encrypt(conn, case_id, pdf, purpose=path))
    await conn.execute("update app.invoices set pdf_path = %s where id = %s", (path, invoice_id))
    await audit.record(conn, action="invoice.sent", target=f"invoice:{invoice_id}", case_id=case_id)
    params = {
        "case_ref": str(case_id),
        "invoice_no": invoice.number_text,
        "amount": f"{invoice.total:.2f}",
        "currency": invoice.currency,
    }
    await current().mailer.send(Mail(to, INVOICE_SENT, params))
    return invoice


@router.get(
    "/cases/{case_id}/invoices/{invoice_id}/pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
async def invoice_pdf(
    case_id: uuid.UUID,
    invoice_id: uuid.UUID,
    access: RecordPayment,
    conn: DbConn,
    pack: Pack,
    crypto: Crypto,
    storage: Storage,
) -> Response:
    invoice = await _one(conn, case_id, invoice_id)
    cur = await conn.execute("select pdf_path from app.invoices where id = %s", (invoice_id,))
    row = await cur.fetchone()
    if row is not None and row[0]:
        data = await crypto.decrypt(conn, case_id, await storage.get(str(row[0])), purpose=str(row[0]))
    else:
        today = datetime.now(ZoneInfo(pack.timezone)).date()
        data = render_invoice(await _document(conn, case_id, pack, invoice, today))
    return Response(data, media_type="application/pdf", headers={"Cache-Control": "private, no-store"})


@router.patch("/cases/{case_id}/invoices/{invoice_id}/payment", response_model=InvoiceOut)
async def record_payment(
    case_id: uuid.UUID, invoice_id: uuid.UUID, body: PaymentIn, access: RecordPayment, conn: DbConn
) -> InvoiceOut:
    invoice = await _one(conn, case_id, invoice_id)
    if invoice.status == "draft":
        raise ApiError("not_sent", "Send the invoice first.", 409)
    paid = invoice.paid_amount
    if body.status == "paid":
        paid = body.paid_amount if body.paid_amount is not None else invoice.total
    elif body.status == "part_paid":
        if body.paid_amount is None or not (0 < body.paid_amount < invoice.total):
            raise invalid("paid_amount")
        paid = body.paid_amount
    else:
        cur = await conn.execute("select app.can_access_case(%s, 'credit_note.issue')", (case_id,))
        allowed = await cur.fetchone()
        if not (allowed and allowed[0]):
            raise ApiError("forbidden", "You cannot do that on this case.", 403)
    await conn.execute(
        "update app.invoices set status = %s, paid_amount = %s, version = version + 1 where id = %s",
        (body.status, paid, invoice_id),
    )
    await audit.record(conn, action="invoice.payment_recorded", target=f"invoice:{invoice_id}", case_id=case_id)
    return await _one(conn, case_id, invoice_id)


@router.post("/cases/{case_id}/invoices/{invoice_id}/credit-notes", response_model=CreditNoteOut, status_code=201)
async def issue_credit_note(
    case_id: uuid.UUID, invoice_id: uuid.UUID, body: CreditIn, access: IssueCredit, conn: DbConn
) -> CreditNoteOut:
    await _one(conn, case_id, invoice_id)
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "insert into app.credit_notes (case_id, invoice_id, amount, reason) values (%s, %s, %s, %s)"
            " returning id, number_text, amount, reason, issued_at",
            (case_id, invoice_id, body.amount, body.reason),
        )
        row = await cur.fetchone()
    assert row is not None
    await audit.record(conn, action="invoice.credit_note_issued", target=f"credit_note:{row['id']}", case_id=case_id)
    return CreditNoteOut(**row)
