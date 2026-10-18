"""The invoice PDF (reportlab): issuer, recipient, reference, lines, tax and total.

Words come from `words.py` by the pack's language and dates follow the pack's date order. The reference is
the instructing party's own reference when they gave one, otherwise the case's id; no clinical words, case
label or patient name appear. Below the total: the time estimate against this invoice's total (when an
estimate was given), the jurisdiction pack's invoice statements (for example that the fee does not depend on
the outcome) and a "How to pay" box when the issuer's bank details were filled in, all as frozen at draft time.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table

from app.features.report.bank import FIELDS as BANK_FIELDS
from app.features.report.words import long_date, word


@dataclass(frozen=True)
class InvoiceDoc:
    language: str
    date_order: str
    number: str | None
    issued_on: date
    issuer: str
    issuer_address: str | None
    tax_number: str | None
    recipient: str | None
    reference: str
    currency: str
    tax_rate: Decimal
    lines: list[dict[str, Any]]
    tax: Decimal
    total: Decimal
    credits: list[tuple[str, Decimal]] = field(default_factory=list)
    statements: list[str] = field(default_factory=list)
    bank: dict[str, str] = field(default_factory=dict)
    estimate: Decimal | None = None


def money(currency: str, amount: Decimal | str) -> str:
    return f"{currency} {Decimal(amount):.2f}"


def render_invoice(doc: InvoiceDoc) -> bytes:
    lang = doc.language
    styles = getSampleStyleSheet()
    title = word(lang, "invoice", number=doc.number) if doc.number else word(lang, "invoice_draft")
    story: list[object] = [Paragraph(escape(title), styles["Heading1"])]
    head = [word(lang, "invoice_from", name=doc.issuer)]
    if doc.issuer_address:
        head.append(doc.issuer_address)
    if doc.tax_number:
        head.append(word(lang, "invoice_tax_number", number=doc.tax_number))
    if doc.recipient:
        head.append(word(lang, "invoice_to", name=doc.recipient))
    head.append(word(lang, "invoice_ref", ref=doc.reference))
    head.append(word(lang, "invoice_date", date=long_date(lang, doc.date_order, doc.issued_on)))
    story.extend(Paragraph(escape(line), styles["BodyText"]) for line in head)
    story.append(Spacer(1, 12))
    rows: list[list[str]] = []
    for line in doc.lines:
        if line["kind"] == "time":
            label = word(lang, "invoice_time", hours=line["hours"], rate=money(doc.currency, line["rate"]))
        else:
            label = word(lang, "invoice_expenses")
        rows.append([label, money(doc.currency, line["amount"])])
    rows.append([word(lang, "invoice_tax", rate=f"{doc.tax_rate:.2f}"), money(doc.currency, doc.tax)])
    rows.append([word(lang, "invoice_total"), money(doc.currency, doc.total)])
    for number, amount in doc.credits:
        rows.append([word(lang, "invoice_credit", number=number), money(doc.currency, -amount)])
    story.append(Table(rows, colWidths=[320, 140]))
    tail: list[str] = []
    if doc.estimate is not None:
        given, billed = money(doc.currency, doc.estimate), money(doc.currency, doc.total)
        tail.append(word(lang, "invoice_estimate", estimate=given, amount=billed))
    tail.extend(doc.statements)
    if tail:
        story.append(Spacer(1, 12))
        story.extend(Paragraph(escape(line), styles["BodyText"]) for line in tail)
    if doc.bank:
        story.append(Spacer(1, 12))
        pay = [[word(lang, "invoice_how_to_pay"), ""]]
        pay.extend([word(lang, f"invoice_bank_{key}"), doc.bank[key]] for key in BANK_FIELDS if key in doc.bank)
        box = Table(pay, colWidths=[160, 300])
        box.setStyle([("BOX", (0, 0), (-1, -1), 0.75, colors.black), ("SPAN", (0, 0), (-1, 0))])
        story.append(box)
    out = io.BytesIO()
    SimpleDocTemplate(out, pagesize=A4, title=title).build(story)
    return out.getvalue()
