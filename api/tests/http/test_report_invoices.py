"""Slice F, invoices (screen 18b): the issuer's billing profile, draft invoices from the expert's time,
sending (step-up, numbered, sealed PDF, a mail with no clinical content), payments and credit notes.

Acceptance (slice F brief, architecture 10.8): a team case's invoice comes from the organisation's billing
profile and a personal case's from the expert's own; numbering is gap-free per issuer and happens when the
invoice is sent; a sent invoice never changes and corrections are credit notes; the invoice uses the
issuer's rate, tax rate and currency; the mail carries only the case's id, number and amount (an instructing
party's own reference can hold a name, so it stays in the PDF);
`app.case_invoice_rows` gives slice H the rows for `invoices.csv`.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

import app.core.http as core_http
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.report.services import ReportServices, configure, current
from tests.fixtures.report.world import CRYPTO, Reads, World, app_for, build
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

RECIPIENT = "zz-office@example.test"


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture(autouse=True)
def services(reads: Reads, storage: CaseFileStorage) -> Iterator[None]:
    configure(ReportServices(crypto=CRYPTO, storage=storage, reads=reads))
    yield
    configure(None)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: CaseFileStorage) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads, storage)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


def org_profile(admin: psycopg.Connection, org: uuid.UUID, **extra: Any) -> uuid.UUID:
    values = {
        "legal_name": "Zz Team Ltd",
        "currency": "XTS",
        "hourly_rate": 180,
        "tax_rate": 20,
        "invoice_prefix": "ZZ-",
        **extra,
    }
    cols = ", ".join(values)
    marks = ", ".join(["%s"] * len(values))
    row = admin.execute(
        f"insert into app.billing_profiles (owner_type, owner_id, {cols}) values ('organisation', %s, {marks})"
        " returning id",
        (org, *values.values()),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def first_step(combo: Combo) -> str:
    return str(default_library().combine(*combo.codes).workflow_steps()[0]["key"])


def add_time(admin: psycopg.Connection, case: uuid.UUID, person: uuid.UUID, seconds: int, stage: str) -> None:
    admin.execute(
        "insert into app.time_entries (case_id, person_id, stage, started_at, ended_at, seconds)"
        " values (%s, %s, %s, now() - make_interval(secs => %s), now(), %s)",
        (case, person, stage, seconds, seconds),
    )


def add_recipient(admin: psycopg.Connection, case: uuid.UUID, email: str | None = RECIPIENT) -> None:
    admin.execute(
        "insert into app.case_instructing_parties (case_id, type_key, name, firm, email, reference)"
        " values (%s, 'zz_party', 'Zz Person', 'Zz Firm', %s, 'ZZ/REF/1')",
        (case, email),
    )


async def drafted(client_for: ClientFor, admin: psycopg.Connection, w: World) -> dict[str, Any]:
    org_profile(admin, w.org)
    add_time(admin, w.case, w.expert, 72900, first_step(w.combo))
    add_recipient(admin, w.case)
    resp = await (await client_for(w.expert)).post(w.url("invoices"), json={"expenses": "0"})
    assert resp.status_code == 201, resp.text
    body: dict[str, Any] = resp.json()
    return body


# Billing profile ------------------------------------------------------------------------------------------


async def test_the_expert_keeps_a_personal_billing_profile_with_step_up_for_the_tax_number(
    client_for: ClientFor, world: World
) -> None:
    client = await client_for(world.expert)
    empty = await client.get("/v1/me/billing-profile")
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"profile": None}
    body = {
        "legal_name": "Zz Expert",
        "address": "1 Zz Road",
        "currency": "XTS",
        "hourly_rate": "150.00",
        "tax_rate": "0",
        "invoice_prefix": "ZE-",
        "tax_number": None,
    }
    resp = await client.put("/v1/me/billing-profile", json=body, headers={"If-Match": empty.headers["etag"]})
    assert resp.status_code == 200, resp.text
    assert resp.json()["profile"]["hourly_rate"] == "150.00"
    tag = resp.headers["etag"]
    stale = await client.put("/v1/me/billing-profile", json=body, headers={"If-Match": empty.headers["etag"]})
    assert stale.status_code == 412

    late = await client_for(world.expert, mfa_age=timedelta(minutes=15))
    taxed = await late.put("/v1/me/billing-profile", json={**body, "tax_number": "ZZ123"}, headers={"If-Match": tag})
    assert taxed.status_code == 403 and taxed.json()["error"]["code"] == "step_up_required"
    moved = await late.put("/v1/me/billing-profile", json={**body, "address": "2 Zz Road"}, headers={"If-Match": tag})
    assert moved.status_code == 200, moved.text
    taxed = await client.put(
        "/v1/me/billing-profile", json={**body, "tax_number": "ZZ123"}, headers={"If-Match": moved.headers["etag"]}
    )
    assert taxed.status_code == 200 and taxed.json()["profile"]["tax_number"] == "ZZ123"


# Drafts ---------------------------------------------------------------------------------------------------


async def test_a_team_case_invoice_comes_from_the_organisations_profile_at_its_rate_and_tax(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    client = await client_for(world.expert)
    missing = await client.post(world.url("invoices"), json={"expenses": "0"})
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "no_billing_profile"

    profile = org_profile(admin, world.org)
    add_time(admin, world.case, world.expert, 72900, first_step(world.combo))
    add_time(admin, world.case, world.assistant, 3600, first_step(world.combo))
    resp = await client.post(world.url("invoices"), json={"expenses": "12.50"})
    assert resp.status_code == 201, resp.text
    inv = resp.json()
    assert inv["status"] == "draft" and inv["number_text"] is None and inv["currency"] == "XTS"
    time_line = next(line for line in inv["lines"] if line["kind"] == "time")
    assert time_line == {"kind": "time", "seconds": 72900, "hours": "20.25", "rate": "180.00", "amount": "3645.00"}
    assert {"kind": "expenses", "amount": "12.50"} in inv["lines"]
    assert inv["tax"] == "731.50" and inv["total"] == "4389.00"
    row = admin.execute("select billing_profile_id from app.invoices where id = %s", (inv["id"],)).fetchone()
    assert row is not None and row[0] == profile

    again = await client.post(world.url("invoices"), json={"expenses": "0"})
    assert again.status_code == 201 and again.json()["id"] == inv["id"], "the open draft is brought up to date"
    listed = await client.get(world.url("invoices"))
    assert listed.status_code == 200 and [i["id"] for i in listed.json()["invoices"]] == [inv["id"]]


async def test_a_personal_case_invoice_comes_from_the_experts_own_profile(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, world: World
) -> None:
    personal = seed.org(kind="personal", members={world.expert: "owner"})
    case = seed.case(personal, world.expert, status="active", packs=world.combo.case_row())
    add_time(admin, case, world.expert, 3600, first_step(world.combo))
    client = await client_for(world.expert)
    url = f"/v1/cases/{case}/invoices"
    assert (await client.post(url, json={"expenses": "0"})).json()["error"]["code"] == "no_billing_profile"
    empty = await client.get("/v1/me/billing-profile")
    put = await client.put(
        "/v1/me/billing-profile",
        json={"legal_name": "Zz Expert", "currency": "XTS", "hourly_rate": "100", "tax_rate": "0"},
        headers={"If-Match": empty.headers["etag"]},
    )
    assert put.status_code == 200, put.text
    resp = await client.post(url, json={"expenses": "0"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["total"] == "100.00" and resp.json()["tax"] == "0.00"


async def test_a_profile_without_an_hourly_rate_cannot_invoice(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    org_profile(admin, world.org, hourly_rate=None)
    resp = await (await client_for(world.expert)).post(world.url("invoices"), json={"expenses": "0"})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "no_hourly_rate"


async def test_who_may_draft_and_see_invoices(client_for: ClientFor, admin: psycopg.Connection, world: World) -> None:
    org_profile(admin, world.org)
    for person in (world.co_expert, world.viewer):
        resp = await (await client_for(person)).post(world.url("invoices"), json={"expenses": "0"})
        assert resp.status_code == 403, person
    resp = await (await client_for(world.outsider)).get(world.url("invoices"))
    assert resp.status_code == 404
    resp = await (await client_for(world.assistant)).post(world.url("invoices"), json={"expenses": "0"})
    assert resp.status_code == 201, resp.text


# Sending --------------------------------------------------------------------------------------------------


async def test_sending_needs_step_up_and_the_named_expert(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv = await drafted(client_for, admin, world)
    url = world.url(f"invoices/{inv['id']}/send")
    late = await client_for(world.expert, mfa_age=timedelta(minutes=15))
    resp = await late.post(url, json={})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "step_up_required"
    resp = await (await client_for(world.assistant)).post(url, json={})
    assert resp.status_code == 403


async def test_sending_numbers_the_invoice_seals_its_pdf_and_mails_no_clinical_content(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv = await drafted(client_for, admin, world)
    client = await client_for(world.expert)
    resp = await client.post(world.url(f"invoices/{inv['id']}/send"), json={})
    assert resp.status_code == 200, resp.text
    sent = resp.json()
    assert sent["status"] == "sent" and sent["number_text"] == "ZZ-1" and sent["sent_to"] == RECIPIENT
    again = await client.post(world.url(f"invoices/{inv['id']}/send"), json={})
    assert again.status_code == 409

    outbox = current().mailer.outbox
    assert len(outbox) == 1
    mail = outbox[0]
    assert mail.to == RECIPIENT and mail.template == "invoice.sent"
    assert mail.params == {"case_ref": str(world.case), "invoice_no": "ZZ-1", "amount": "4374.00", "currency": "XTS"}
    label = admin.execute("select label from app.cases where id = %s", (world.case,)).fetchone()
    assert label is not None and all(str(label[0]) not in v for v in mail.params.values())

    row = admin.execute("select pdf_path from app.invoices where id = %s", (inv["id"],)).fetchone()
    assert row is not None and row[0]
    pdf = await client.get(world.url(f"invoices/{inv['id']}/pdf"))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    assert pdf.headers["cache-control"] == "private, no-store"

    second = await client.post(world.url("invoices"), json={"expenses": "0"})
    assert second.status_code == 201 and second.json()["id"] != inv["id"], "a sent invoice is never redrafted"
    resp = await client.post(world.url(f"invoices/{second.json()['id']}/send"), json={"to": "zz-other@example.test"})
    assert resp.status_code == 200 and resp.json()["number_text"] == "ZZ-2", "numbering has no gaps"
    assert resp.json()["sent_to"] == "zz-other@example.test"


async def test_agency_and_personal_invoice_numbers_run_separately_without_gaps(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, world: World
) -> None:
    personal = seed.org(kind="personal", members={world.expert: "owner"})
    solo = seed.case(personal, world.expert, status="active", packs=world.combo.case_row())
    admin.execute(
        "insert into app.billing_profiles (owner_type, owner_id, legal_name, currency, hourly_rate, invoice_prefix)"
        " values ('person', %s, 'Zz Expert', 'XTS', 100, 'PE-')",
        (world.expert,),
    )
    add_time(admin, solo, world.expert, 3600, first_step(world.combo))
    add_recipient(admin, solo)
    await drafted(client_for, admin, world)
    client = await client_for(world.expert)
    sent = []
    for case in (solo, world.case, solo, world.case):
        draft = await client.post(f"/v1/cases/{case}/invoices", json={"expenses": "0"})
        assert draft.status_code == 201, draft.text
        resp = await client.post(f"/v1/cases/{case}/invoices/{draft.json()['id']}/send", json={})
        assert resp.status_code == 200, resp.text
        sent.append(resp.json()["number_text"])
    assert sent == ["PE-1", "ZZ-1", "PE-2", "ZZ-2"]


async def test_sending_without_a_recipient_is_refused(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    org_profile(admin, world.org)
    add_time(admin, world.case, world.expert, 3600, first_step(world.combo))
    add_recipient(admin, world.case, email=None)
    client = await client_for(world.expert)
    inv = (await client.post(world.url("invoices"), json={"expenses": "0"})).json()
    resp = await client.post(world.url(f"invoices/{inv['id']}/send"), json={})
    assert resp.status_code == 422
    assert current().mailer.outbox == []
    row = admin.execute("select status, number from app.invoices where id = %s", (inv["id"],)).fetchone()
    assert row == ("draft", None)


async def test_a_draft_pdf_can_be_previewed(client_for: ClientFor, admin: psycopg.Connection, world: World) -> None:
    inv = await drafted(client_for, admin, world)
    pdf = await (await client_for(world.expert)).get(world.url(f"invoices/{inv['id']}/pdf"))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    resp = await (await client_for(world.viewer)).get(world.url(f"invoices/{inv['id']}/pdf"))
    assert resp.status_code in (403, 404)


# Payments and credit notes --------------------------------------------------------------------------------


async def test_payment_status_is_recorded_on_sent_invoices(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv = await drafted(client_for, admin, world)
    assistant = await client_for(world.assistant)
    url = world.url(f"invoices/{inv['id']}/payment")
    draft = await assistant.patch(url, json={"status": "paid"})
    assert draft.status_code == 409 and draft.json()["error"]["code"] == "not_sent"
    await (await client_for(world.expert)).post(world.url(f"invoices/{inv['id']}/send"), json={})

    viewer = await (await client_for(world.viewer)).patch(url, json={"status": "paid"})
    assert viewer.status_code == 403
    part = await assistant.patch(url, json={"status": "part_paid", "paid_amount": "100.00"})
    assert part.status_code == 200 and part.json()["status"] == "part_paid", part.text
    bad = await assistant.patch(url, json={"status": "part_paid", "paid_amount": "9999.00"})
    assert bad.status_code == 422
    off = await assistant.patch(url, json={"status": "written_off"})
    assert off.status_code == 403, "writing off needs credit_note.issue"
    paid = await assistant.patch(url, json={"status": "paid"})
    assert paid.status_code == 200 and paid.json()["paid_amount"] == "4374.00"


async def test_credit_notes_correct_a_sent_invoice_up_to_its_total(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv = await drafted(client_for, admin, world)
    expert = await client_for(world.expert)
    url = world.url(f"invoices/{inv['id']}/credit-notes")
    draft = await expert.post(url, json={"amount": "10.00", "reason": "zz_reason"})
    assert draft.status_code == 409
    await expert.post(world.url(f"invoices/{inv['id']}/send"), json={})

    late = await client_for(world.expert, mfa_age=timedelta(minutes=15))
    resp = await late.post(url, json={"amount": "10.00", "reason": "zz_reason"})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "step_up_required"
    assert (await (await client_for(world.assistant)).post(url, json={"amount": "1", "reason": "x"})).status_code == 403
    resp = await expert.post(url, json={"amount": "10.00", "reason": "zz_reason"})
    assert resp.status_code == 201, resp.text
    assert resp.json()["number_text"] == "ZZ-CN1" and resp.json()["amount"] == "10.00"
    over = await expert.post(url, json={"amount": "4364.01", "reason": "zz_reason"})
    assert over.status_code == 409
    listed = (await expert.get(world.url("invoices"))).json()["invoices"][0]
    assert [c["number_text"] for c in listed["credit_notes"]] == ["ZZ-CN1"]
    audit = admin.execute(
        "select action from app.audit_log where case_id = %s and action like 'invoice.%%' order by seq",
        (world.case,),
    ).fetchall()
    assert [a[0] for a in audit] == ["invoice.sent", "invoice.credit_note_issued"]


async def test_slice_h_reads_sent_invoices_for_the_export(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv = await drafted(client_for, admin, world)
    expert = await client_for(world.expert)
    await expert.post(world.url(f"invoices/{inv['id']}/send"), json={})
    await expert.post(world.url(f"invoices/{inv['id']}/credit-notes"), json={"amount": "4.00", "reason": "zz_reason"})
    await expert.post(world.url("invoices"), json={"expenses": "0"})
    rows = admin.execute(
        "select number_text, status, total, tax, currency, paid_amount, credited from app.case_invoice_rows(%s)",
        (world.case,),
    ).fetchall()
    assert [(r[0], r[1], str(r[2]), str(r[3]), r[4], str(r[5]), str(r[6])) for r in rows] == [
        ("ZZ-1", "sent", "4374.00", "729.00", "XTS", "0.00", "4.00")
    ], "drafts are not exported"
    grants = admin.execute(
        "select has_function_privilege('app_worker', 'app.case_invoice_rows(uuid)', 'execute'),"
        " has_function_privilege('app_api', 'app.case_invoice_rows(uuid)', 'execute')"
    ).fetchone()
    assert grants == (True, True)


# Sending once, after commit; writing off with step-up (review findings 2, 3 and 8) ------------------------


async def test_two_sends_at_once_number_once_and_mail_once(
    client_for: ClientFor,
    admin: psycopg.Connection,
    storage: CaseFileStorage,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inv = await drafted(client_for, admin, world)
    real_put = storage.put

    async def slow_put(path: str, data: bytes) -> None:
        await asyncio.sleep(0.3)  # the first send is still open when the second arrives
        await real_put(path, data)

    monkeypatch.setattr(storage, "put", slow_put)
    url = world.url(f"invoices/{inv['id']}/send")
    first, second = await client_for(world.expert), await client_for(world.expert)
    answers = await asyncio.gather(first.post(url, json={}), second.post(url, json={}))
    assert sorted(a.status_code for a in answers) == [200, 409], [a.text for a in answers]
    assert next(a for a in answers if a.status_code == 409).json()["error"]["code"] == "already_sent"
    assert len(current().mailer.outbox) == 1
    sent = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'invoice.sent'", (world.case,)
    ).fetchone()
    assert sent == (1,)


async def test_a_send_that_does_not_commit_mails_nobody(
    client_for: ClientFor, admin: psycopg.Connection, world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    inv = await drafted(client_for, admin, world)

    async def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the transaction does not commit")

    monkeypatch.setattr(core_http, "_remember", fail)
    client = await client_for(world.expert)
    with contextlib.suppress(RuntimeError):
        resp = await client.post(
            world.url(f"invoices/{inv['id']}/send"), json={}, headers={"Idempotency-Key": "zz-send-once"}
        )
        assert resp.status_code >= 500
    assert current().mailer.outbox == []
    row = admin.execute("select status, number from app.invoices where id = %s", (inv["id"],)).fetchone()
    assert row == ("draft", None)


async def test_writing_off_needs_step_up(client_for: ClientFor, admin: psycopg.Connection, world: World) -> None:
    inv = await drafted(client_for, admin, world)
    expert = await client_for(world.expert)
    assert (await expert.post(world.url(f"invoices/{inv['id']}/send"), json={})).status_code == 200
    url = world.url(f"invoices/{inv['id']}/payment")
    late = await client_for(world.expert, mfa_age=timedelta(minutes=15))
    resp = await late.patch(url, json={"status": "written_off"})
    assert resp.status_code == 403 and resp.json()["error"]["code"] == "step_up_required"
    row = admin.execute("select status from app.invoices where id = %s", (inv["id"],)).fetchone()
    assert row == ("sent",)
    resp = await expert.patch(url, json={"status": "written_off"})
    assert resp.status_code == 200 and resp.json()["status"] == "written_off", resp.text


# Payments add up; the sent PDF keeps what was drafted (review findings 4 and 5) ---------------------------


async def sent(client_for: ClientFor, admin: psycopg.Connection, w: World) -> tuple[dict[str, Any], Any]:
    inv = await drafted(client_for, admin, w)
    expert = await client_for(w.expert)
    resp = await expert.post(w.url(f"invoices/{inv['id']}/send"), json={})
    assert resp.status_code == 200, resp.text
    return inv, expert


async def test_paid_with_no_amount_pays_what_is_left_after_payments_and_credits(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv, expert = await sent(client_for, admin, world)
    credit = await expert.post(
        world.url(f"invoices/{inv['id']}/credit-notes"), json={"amount": "10.00", "reason": "zz_reason"}
    )
    assert credit.status_code == 201, credit.text
    url = world.url(f"invoices/{inv['id']}/payment")
    assert (await expert.patch(url, json={"status": "part_paid", "paid_amount": "100.00"})).status_code == 200
    paid = await expert.patch(url, json={"status": "paid"})
    assert paid.status_code == 200, paid.text
    assert (paid.json()["status"], paid.json()["paid_amount"]) == ("paid", "4364.00")
    payments = admin.execute(
        "select amount from app.invoice_payments where invoice_id = %s order by recorded_at, amount", (inv["id"],)
    ).fetchall()
    assert [str(p[0]) for p in payments] == ["100.00", "4264.00"]


async def test_payments_are_bounded_and_the_status_follows_the_sums(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv, expert = await sent(client_for, admin, world)
    url = world.url(f"invoices/{inv['id']}/payment")
    for body in (
        {"status": "part_paid", "paid_amount": "0.00"},
        {"status": "part_paid"},
        {"status": "part_paid", "paid_amount": "4374.00"},
        {"status": "paid", "paid_amount": "4374.01"},
        {"status": "paid", "paid_amount": "0.00"},
    ):
        resp = await expert.patch(url, json=body)
        assert resp.status_code == 422, (body, resp.text)
    smaller = await expert.patch(url, json={"status": "paid", "paid_amount": "374.00"})
    assert smaller.status_code == 200, smaller.text
    assert (smaller.json()["status"], smaller.json()["paid_amount"]) == ("part_paid", "374.00")
    over = await expert.patch(url, json={"status": "part_paid", "paid_amount": "4000.00"})
    assert over.status_code == 422, "a part payment leaves something owed"
    rest = await expert.patch(url, json={"status": "paid", "paid_amount": "4000.00"})
    assert (rest.json()["status"], rest.json()["paid_amount"]) == ("paid", "4374.00")
    again = await expert.patch(url, json={"status": "part_paid", "paid_amount": "1.00"})
    assert again.status_code == 409, "a settled invoice takes no more payments"
    off = await expert.patch(url, json={"status": "written_off"})
    assert off.status_code == 409


async def test_the_export_rows_add_up_payments_and_credits(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv, expert = await sent(client_for, admin, world)
    for amount in ("4.00", "6.00"):
        resp = await expert.post(
            world.url(f"invoices/{inv['id']}/credit-notes"), json={"amount": amount, "reason": "zz_reason"}
        )
        assert resp.status_code == 201, resp.text
    url = world.url(f"invoices/{inv['id']}/payment")
    for amount in ("100.00", "200.00"):
        resp = await expert.patch(url, json={"status": "part_paid", "paid_amount": amount})
        assert resp.status_code == 200, resp.text
    rows = admin.execute(
        "select status, paid_amount, credited from app.case_invoice_rows(%s)", (world.case,)
    ).fetchall()
    assert [(r[0], str(r[1]), str(r[2])) for r in rows] == [("part_paid", "300.00", "10.00")]


async def test_the_sent_pdf_keeps_the_tax_rate_and_issuer_of_the_draft(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    import io

    from pypdf import PdfReader

    inv = await drafted(client_for, admin, world)
    admin.execute(
        "update app.billing_profiles set tax_rate = 0, legal_name = 'Zz Renamed Ltd'"
        " where owner_type = 'organisation' and owner_id = %s",
        (world.org,),
    )
    expert = await client_for(world.expert)
    resp = await expert.post(world.url(f"invoices/{inv['id']}/send"), json={})
    assert resp.status_code == 200, resp.text
    pdf = await expert.get(world.url(f"invoices/{inv['id']}/pdf"))
    assert pdf.status_code == 200
    text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(pdf.content)).pages)
    assert "Zz Team Ltd" in text and "Zz Renamed Ltd" not in text
    assert "20.00" in text and "XTS 729.00" in text
