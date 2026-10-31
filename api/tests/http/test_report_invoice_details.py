"""Slice F, invoice details approved on 2026-10-18 (screen 18b): the pack's invoice statements, bank details
on the billing profile and the time estimate.

- The jurisdiction pack's invoice statements (fee independence at launch) print on the invoice, frozen when it
  is drafted; a pack without any prints nothing.
- Bank details (account name, sort code, account number, IBAN, SWIFT/BIC) are optional on the billing profile;
  the expert edits their own, an organisation owner or admin the organisation's; every change needs step-up
  and writes an audit row with no values. They print in a "How to pay" box only when filled in, from the
  issuer snapshot taken at draft time, and never go in any email.
- The time estimate is kept per workflow step as append-only rows (`invoice.create`); `GET .../time` gives the
  estimate against the actual time per step and, once the time is over the estimate by more than the pack's
  threshold, a notice naming the instructing party in the pack's word. The invoice states the estimate
  against its own total, frozen at draft time, in the invoice's currency.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import timedelta

import psycopg
import pytest
from fastapi import FastAPI
from pypdf import PdfReader

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.report.services import ReportServices, configure, current
from app.features.report.words import word
from tests.fixtures.report.world import CRYPTO, Reads, World, app_for, build
from tests.http.test_report_invoices import add_recipient, add_time, drafted, first_step, org_profile
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

BANK_IN = {
    "account_name": "Zz Practice Ltd",
    "sort_code": "12-34-56",
    "account_number": "1234 5678",
    "iban": "gb82 west 1234 5698 7654 32",
    "bic": "nwbkgb2l",
}
BANK = {
    "account_name": "Zz Practice Ltd",
    "sort_code": "123456",
    "account_number": "12345678",
    "iban": "GB82WEST12345698765432",
    "bic": "NWBKGB2L",
}


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


def steps(combo: Combo) -> list[str]:
    return [str(s["key"]) for s in default_library().combine(*combo.codes).workflow_steps()]


def pdf_text(data: bytes) -> str:
    text = " ".join(page.extract_text() for page in PdfReader(io.BytesIO(data)).pages)
    return " ".join(text.split())


async def draft_pdf(client_for: ClientFor, w: World, invoice_id: str) -> str:
    pdf = await (await client_for(w.expert)).get(w.url(f"invoices/{invoice_id}/pdf"))
    assert pdf.status_code == 200, pdf.text
    return pdf_text(pdf.content)


# Invoice statements ---------------------------------------------------------------------------------------


async def test_the_invoice_prints_the_packs_statements_frozen_when_drafted(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    statements = default_library().combine(*world.combo.codes).invoice_statements()
    inv = await drafted(client_for, admin, world)
    assert inv["statements"] == statements
    row = admin.execute("select statements from app.invoices where id = %s", (inv["id"],)).fetchone()
    assert row is not None and row[0] == statements, "frozen into the invoice when drafted"
    text = await draft_pdf(client_for, world, inv["id"])
    for line in statements:
        assert line in text
    if not statements:
        raw = world.combo.raw("jurisdiction", "invoice.yaml") or {}
        assert raw.get("statements", []) == [], "a pack without statements prints none"


async def test_a_sent_invoice_keeps_the_statements_it_was_drafted_with(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv = await drafted(client_for, admin, world)
    admin.execute("alter table app.invoices disable trigger invoices_guard")
    admin.execute("update app.invoices set statements = '[\"Zz frozen line.\"]' where id = %s", (inv["id"],))
    admin.execute("alter table app.invoices enable trigger invoices_guard")
    expert = await client_for(world.expert)
    resp = await expert.post(world.url(f"invoices/{inv['id']}/send"), json={})
    assert resp.status_code == 200, resp.text
    pdf = await expert.get(world.url(f"invoices/{inv['id']}/pdf"))
    assert "Zz frozen line." in pdf_text(pdf.content), "the PDF prints the snapshot, not the pack of the day"


# Bank details ---------------------------------------------------------------------------------------------


async def own_profile(client_for: ClientFor, person: uuid.UUID) -> str:
    client = await client_for(person)
    empty = await client.get("/v1/me/billing-profile")
    resp = await client.put(
        "/v1/me/billing-profile",
        json={"legal_name": "Zz Expert", "currency": "XTS", "hourly_rate": "100", "tax_rate": "0"},
        headers={"If-Match": empty.headers["etag"]},
    )
    assert resp.status_code == 200, resp.text
    return str(resp.headers["etag"])


async def test_the_expert_adds_bank_details_with_step_up_and_an_audit_row_without_values(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    client = await client_for(world.expert)
    none = await client.put("/v1/me/billing-profile/bank-details", json=BANK_IN, headers={"If-Match": '"0"'})
    assert none.status_code == 409 and none.json()["error"]["code"] == "no_billing_profile"
    tag = await own_profile(client_for, world.expert)

    late = await client_for(world.expert, mfa_age=timedelta(minutes=15))
    refused = await late.put("/v1/me/billing-profile/bank-details", json=BANK_IN, headers={"If-Match": tag})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "step_up_required"

    resp = await client.put("/v1/me/billing-profile/bank-details", json=BANK_IN, headers={"If-Match": tag})
    assert resp.status_code == 200, resp.text
    assert resp.json()["bank"] == BANK, "spaces and dashes dropped, letters in capitals"
    stale = await client.put("/v1/me/billing-profile/bank-details", json=BANK_IN, headers={"If-Match": tag})
    assert stale.status_code == 412
    shown = await client.get("/v1/me/billing-profile")
    assert shown.json()["profile"]["bank"] == BANK
    assert shown.headers["etag"] == resp.headers["etag"]

    cleared = await client.put(
        "/v1/me/billing-profile/bank-details", json={}, headers={"If-Match": resp.headers["etag"]}
    )
    assert cleared.status_code == 200 and cleared.json()["bank"] is None, "every field is optional"

    rows = admin.execute(
        # timestamps, hashes and ids are random and can contain the digits by chance
        "select (to_jsonb(a) - 'at' - 'seq' - 'hash' - 'prev_hash' - 'target' - 'person_id')::text"
        " from app.audit_log a where action = 'billing_profile.bank_details_changed'"
        " and person_id = %s",
        (world.expert,),
    ).fetchall()
    assert len(rows) == 2
    for (row,) in rows:
        for value in (*BANK.values(), "5678", "WEST"):
            assert value not in row, "the audit row says what changed, never the values"


@pytest.mark.parametrize(
    "field,value",
    [
        ("sort_code", "12ab56"),
        ("sort_code", "1234567"),
        ("account_number", "12-34-x"),
        ("iban", "GB82"),
        ("iban", "1234WEST12345698765432"),
        ("bic", "NW1"),
        ("account_name", "z" * 141),
    ],
)
async def test_bank_details_are_checked_loosely(client_for: ClientFor, world: World, field: str, value: str) -> None:
    tag = await own_profile(client_for, world.expert)
    client = await client_for(world.expert)
    resp = await client.put(
        "/v1/me/billing-profile/bank-details", json={**BANK_IN, field: value}, headers={"If-Match": tag}
    )
    assert resp.status_code == 422, resp.text


async def test_only_an_organisation_owner_or_admin_changes_the_organisations_bank_details(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, world: World
) -> None:
    url = f"/v1/organisations/{world.org}/billing-profile/bank-details"
    org_admin, owner = seed.person(), seed.person()
    seed.membership(world.org, org_admin, "admin")
    seed.membership(world.org, owner, "owner")
    missing = await (await client_for(owner)).get(url)
    assert missing.status_code == 409 and missing.json()["error"]["code"] == "no_billing_profile"
    org_profile(admin, world.org)

    for person in (world.expert, world.assistant, world.outsider):
        client = await client_for(person)
        assert (await client.get(url)).status_code == 404, person
        assert (await client.put(url, json=BANK_IN, headers={"If-Match": '"1"'})).status_code == 404, person

    admin_client = await client_for(org_admin)
    got = await admin_client.get(url)
    assert got.status_code == 200 and got.json() == {"bank": None}
    late = await client_for(org_admin, mfa_age=timedelta(minutes=15))
    refused = await late.put(url, json=BANK_IN, headers={"If-Match": got.headers["etag"]})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "step_up_required"
    saved = await admin_client.put(url, json=BANK_IN, headers={"If-Match": got.headers["etag"]})
    assert saved.status_code == 200, saved.text
    assert saved.json() == {"bank": BANK}
    owner_client = await client_for(owner)
    changed = await owner_client.put(
        url, json={**BANK_IN, "iban": None, "bic": None}, headers={"If-Match": saved.headers["etag"]}
    )
    assert changed.status_code == 200 and changed.json()["bank"]["iban"] is None
    audit = admin.execute(
        "select count(*) from app.audit_log where action = 'billing_profile.bank_details_changed'"
        " and organisation_id = %s",
        (world.org,),
    ).fetchone()
    assert audit is not None and audit[0] == 2


async def test_how_to_pay_prints_the_bank_details_of_the_draft_and_only_when_filled_in(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    how = word("en", "invoice_how_to_pay")
    plain = await drafted(client_for, admin, world)
    assert how not in await draft_pdf(client_for, world, plain["id"])

    admin.execute(
        "update app.billing_profiles set bank_account_name = %s, bank_sort_code = %s, bank_account_number = %s,"
        " bank_iban = %s, bank_bic = %s where owner_type = 'organisation' and owner_id = %s",
        (*BANK.values(), world.org),
    )
    resp = await (await client_for(world.expert)).post(world.url("invoices"), json={"expenses": "0"})
    assert resp.status_code == 201 and resp.json()["id"] == plain["id"]
    admin.execute(
        "update app.billing_profiles set bank_account_number = '99999999' where owner_type = 'organisation'"
        " and owner_id = %s",
        (world.org,),
    )
    text = await draft_pdf(client_for, world, plain["id"])
    assert how in text
    for value in BANK.values():
        assert value in text
    assert "99999999" not in text, "the box shows the bank details as they were when drafted"


async def test_the_invoice_email_never_carries_bank_details(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    org_profile(
        admin,
        world.org,
        bank_account_name=BANK["account_name"],
        bank_sort_code=BANK["sort_code"],
        bank_account_number=BANK["account_number"],
        bank_iban=BANK["iban"],
        bank_bic=BANK["bic"],
    )
    add_time(admin, world.case, world.expert, 3600, first_step(world.combo))
    add_recipient(admin, world.case)
    expert = await client_for(world.expert)
    inv = (await expert.post(world.url("invoices"), json={"expenses": "0"})).json()
    resp = await expert.post(world.url(f"invoices/{inv['id']}/send"), json={})
    assert resp.status_code == 200, resp.text
    [mail] = current().mailer.outbox
    assert mail.template == "invoice.sent"
    everything = f"{mail.to} {mail.template} {mail.params}"
    for value in BANK.values():
        assert value not in everything


# Time estimate --------------------------------------------------------------------------------------------


async def test_the_estimate_is_kept_per_step_as_new_rows_and_shown_against_the_time(
    client_for: ClientFor, admin: psycopg.Connection, world: World, api_dsn: str
) -> None:
    keys = steps(world.combo)
    expert = await client_for(world.expert)
    first = await expert.post(world.url("time/estimates"), json={"estimates": [{"stage": keys[0], "hours": "2"}]})
    assert first.status_code == 200, first.text
    second = await expert.post(
        world.url("time/estimates"),
        json={"estimates": [{"stage": keys[0], "hours": "4.5"}, {"stage": keys[-1], "hours": "1", "fee": "250.00"}]},
    )
    assert second.status_code == 200, second.text
    rows = admin.execute(
        "select stage, hours, fee from app.time_estimates where case_id = %s order by created_at, stage",
        (world.case,),
    ).fetchall()
    assert len(rows) == 3, "a change is a new row, never an update"
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        with psycopg.connect(api_dsn) as api:
            api.execute("update app.time_estimates set hours = 0")

    body = (await expert.get(world.url("time"))).json()
    by_stage = {s["stage"]: s for s in body["stages"]}
    assert by_stage[keys[0]]["estimate_hours"] == "4.50"
    assert by_stage[keys[-1]]["estimate_hours"] == "1.00" and by_stage[keys[-1]]["estimate_fee"] == "250.00"
    middle = [k for k in keys if k not in (keys[0], keys[-1])]
    assert all(by_stage[k]["estimate_hours"] is None for k in middle)
    assert body["estimate_hours"] == "5.50"
    assert body["over_estimate"] is None


async def test_an_estimate_names_a_pack_step_and_needs_invoice_create(client_for: ClientFor, world: World) -> None:
    keys = steps(world.combo)
    expert = await client_for(world.expert)
    unknown = await expert.post(world.url("time/estimates"), json={"estimates": [{"stage": "zz_nope", "hours": "1"}]})
    assert unknown.status_code == 422
    negative = await expert.post(world.url("time/estimates"), json={"estimates": [{"stage": keys[0], "hours": "-1"}]})
    assert negative.status_code == 422
    twice = await expert.post(
        world.url("time/estimates"),
        json={"estimates": [{"stage": keys[0], "hours": "1"}, {"stage": keys[0], "hours": "2"}]},
    )
    assert twice.status_code == 422
    body = {"estimates": [{"stage": keys[0], "hours": "1"}]}
    for person in (world.co_expert, world.viewer):
        assert (await (await client_for(person)).post(world.url("time/estimates"), json=body)).status_code == 403
    assert (await (await client_for(world.outsider)).post(world.url("time/estimates"), json=body)).status_code == 404


async def test_time_over_the_estimate_by_more_than_the_packs_threshold_shows_a_gentle_notice(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    pack = default_library().combine(*world.combo.codes)
    percent = pack.estimate_overrun_percent()
    assert percent is not None
    key = steps(world.combo)[0]
    expert = await client_for(world.expert)
    await expert.post(world.url("time/estimates"), json={"estimates": [{"stage": key, "hours": "10"}]})
    limit = 36000 * (100 + percent) // 100
    add_time(admin, world.case, world.expert, limit, key)
    assert (await expert.get(world.url("time"))).json()["over_estimate"] is None, "exactly at the threshold is fine"
    add_time(admin, world.case, world.expert, 60, key)
    over = (await expert.get(world.url("time"))).json()["over_estimate"]
    assert over == {"percent": percent, "instructing_party": pack.instructing_party_types()[0]["label"]}
    inv = await (await client_for(world.expert)).get(world.url("invoices"))
    assert inv.status_code == 200, "the notice never blocks anything"


async def test_the_invoice_states_the_estimate_against_its_total_frozen_when_drafted(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    keys = steps(world.combo)
    expert = await client_for(world.expert)
    resp = await expert.post(
        world.url("time/estimates"),
        json={"estimates": [{"stage": keys[0], "hours": "10"}, {"stage": keys[-1], "hours": "2", "fee": "500"}]},
    )
    assert resp.status_code == 200, resp.text
    inv = await drafted(client_for, admin, world)  # 20.25 h at 180 plus 20% tax: XTS 4374.00
    assert inv["estimate_amount"] == "2300.00", "10 h at the rate plus the fixed fee"
    await expert.post(world.url("time/estimates"), json={"estimates": [{"stage": keys[0], "hours": "50"}]})
    text = await draft_pdf(client_for, world, inv["id"])
    assert word("en", "invoice_estimate", estimate="XTS 2300.00", amount="XTS 4374.00") in text
    assert "£" not in text


async def test_an_invoice_without_an_estimate_says_nothing_about_one(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    inv = await drafted(client_for, admin, world)
    assert inv["estimate_amount"] is None
    text = await draft_pdf(client_for, world, inv["id"])
    assert word("en", "invoice_estimate", estimate="", amount="").split(":")[0] not in text
