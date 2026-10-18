"""Slice F's tables (migrations 0600-0699, architecture 4.7 and 4.8).

Row-level security repeats the case access check (rule 9): people on the case see the report's rows,
people outside it and the bare API role see nothing. The built report's paragraphs and citations, and
final check results, come only from the worker; snapshots, servings and credit notes are never changed
or deleted. A serving needs a served report version. Invoices are numbered when sent, from the issuer's
billing profile, without gaps; a sent invoice never changes except along its payment states, and a
correction is a credit note. Patient words (paragraph text, quoted values) are stored encrypted, so
these tests write placeholder bytes.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.support.contracts import add_fact, add_question, add_report_version
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PARAGRAPH = (
    "insert into app.report_paragraphs (case_id, report_version_id, para_no, section, content_control_id, text,"
    " question_id) values (%(c)s, %(r)s, 1, 'opinion', 'cc1', '\\x00', %(q)s) returning id"
)
CITATION = (
    "insert into app.citations (case_id, report_version_id, paragraph_id, cite_no, fact_id, fact_version,"
    " value_snapshot, quote_verbatim, page_id) values (%(c)s, %(r)s, %(para)s, 1, %(f)s, 2, '\\x00', '\\x00', %(p)s)"
)
CHECK_RUN = (
    "insert into app.final_check_runs (case_id, report_version_id, results, passed)"
    " values (%(c)s, %(r)s, '[]', true)"
)
SNAPSHOT = (
    "insert into app.report_snapshots (case_id, report_version_id, kind, sha256)"
    " values (%(c)s, %(r)s, 'pack_versions', repeat('a', 64))"
)
SERVING = (
    "insert into app.report_servings (case_id, report_version_id, served_on, sent_via)"
    " values (%(c)s, %(r)s, current_date, 'email') returning id"
)
TIME = (
    "insert into app.time_entries (case_id, person_id, stage, started_at, ended_at, seconds)"
    " values (%(c)s, %(who)s, 'reading', now() - interval '1 minute', now(), 60)"
)
INVOICE = (
    "insert into app.invoices (case_id, billing_profile_id, lines, total, tax, currency)"
    " values (%(c)s, %(b)s, '[]', 100.00, 0, 'XTS') returning id"
)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, co_expert, assistant, viewer, outsider = (seed.person() for _ in range(5))
    org = seed.org(
        kind="team",
        members={expert: "expert", co_expert: "expert", assistant: "assistant", viewer: "expert"},
    )
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(
        org,
        expert,
        status="active",
        members={co_expert: "co_expert", assistant: "assistant", viewer: "viewer"},
    )
    page = seed.page(case, seed.document(case), 1)
    row = admin.execute(
        "insert into app.billing_profiles (owner_type, owner_id, legal_name, currency)"
        " values ('person', %s, 'Z Expert', 'XTS') returning id",
        (expert,),
    ).fetchone()
    assert row is not None
    return {
        "expert": expert,
        "co_expert": co_expert,
        "assistant": assistant,
        "viewer": viewer,
        "outsider": outsider,
        "case": case,
        "page": page,
        "fact": add_fact(admin, case, page, status="verified"),
        "question": add_question(admin, case, status="confirmed"),
        "draft": add_report_version(admin, case),
        "profile": row[0],
    }


def args(world: dict[str, uuid.UUID], **more: uuid.UUID) -> dict[str, uuid.UUID]:
    return {
        "c": world["case"],
        "r": world["draft"],
        "q": world["question"],
        "f": world["fact"],
        "p": world["page"],
        "b": world["profile"],
        "who": world["expert"],
        **more,
    }


@contextmanager
def refused() -> Iterator[None]:
    """The database refuses the change (`app.refuse`, SQLSTATE AV409)."""
    with pytest.raises(psycopg.Error) as caught:
        yield
    assert caught.value.sqlstate == "AV409", caught.value.sqlstate


def one(admin: psycopg.Connection, sql: str, values: Mapping[str, Any]) -> uuid.UUID:
    row = admin.execute(sql, values).fetchone()
    assert row is not None
    found: uuid.UUID = row[0]
    return found


async def run_as(
    api_db: Database, person: uuid.UUID, sql: str, values: Mapping[str, Any] | None = None
) -> list[tuple[object, ...]]:
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute(sql, values)
        return await cur.fetchall() if cur.description else []


def fill(admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str) -> None:
    if table in ("report_paragraphs", "citations"):
        paragraph = one(admin, PARAGRAPH, args(world))
        if table == "citations":
            admin.execute(CITATION, args(world, para=paragraph))
    elif table == "report_servings":
        served = add_report_version(admin, world["case"], version_no=2, status="served")
        admin.execute(SERVING, args(world, r=served))
    else:
        admin.execute({"final_check_runs": CHECK_RUN, "report_snapshots": SNAPSHOT}[table], args(world))


@pytest.mark.parametrize(
    "table", ["report_paragraphs", "citations", "final_check_runs", "report_snapshots", "report_servings"]
)
async def test_people_on_the_case_see_report_rows_and_others_do_not(
    api_db: Database, api_dsn: str, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    fill(admin, world, table)
    count = f"select count(*) from app.{table}"  # noqa: S608
    for person in ("expert", "co_expert", "assistant", "viewer"):
        assert await run_as(api_db, world[person], count) == [(1,)], person
    assert await run_as(api_db, world["outsider"], count) == [(0,)]
    with psycopg.connect(api_dsn) as bare:
        assert bare.execute(count).fetchone() == (0,)


@pytest.mark.parametrize("sql", [PARAGRAPH, CHECK_RUN])
async def test_built_paragraphs_and_check_results_come_only_from_the_worker(
    api_db: Database, world: dict[str, uuid.UUID], sql: str
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["expert"], sql, args(world))


@pytest.mark.parametrize(
    "table", ["report_paragraphs", "citations", "final_check_runs", "report_snapshots", "report_servings"]
)
async def test_report_rows_are_never_changed_or_deleted(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID], table: str
) -> None:
    fill(admin, world, table)
    for sql in (f"update app.{table} set created_at = now()", f"delete from app.{table}"):  # noqa: S608
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world["expert"], sql)


async def test_a_paragraph_cannot_be_added_to_a_locked_version(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    signed = add_report_version(admin, world["case"], version_no=3, status="locked")
    with refused():
        admin.execute(PARAGRAPH, args(world, r=signed))


async def test_only_the_named_expert_records_a_serving_and_only_of_a_served_version(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    served = add_report_version(admin, world["case"], version_no=2, status="served")
    for person in ("co_expert", "assistant", "viewer", "outsider"):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await run_as(api_db, world[person], SERVING, args(world, r=served))
    with refused():
        await run_as(api_db, world["expert"], SERVING, args(world))
    await run_as(api_db, world["expert"], SERVING, args(world, r=served))


async def test_a_serving_is_corrected_by_a_new_row_with_a_reason(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    served = add_report_version(admin, world["case"], version_no=2, status="served")
    first = one(admin, SERVING, args(world, r=served))
    correction = (
        "insert into app.report_servings (case_id, report_version_id, served_on, sent_via, correction_of, reason)"
        " values (%(c)s, %(r)s, current_date, 'post', %(x)s, {reason})"
    )
    with pytest.raises(psycopg.errors.CheckViolation):
        admin.execute(correction.format(reason="null"), args(world, r=served, x=first))
    admin.execute(correction.format(reason="'wrong_date'"), args(world, r=served, x=first))


async def test_time_is_recorded_for_yourself_and_seen_by_the_people_who_invoice(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["outsider"], TIME, args(world, who=world["outsider"]))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await run_as(api_db, world["co_expert"], TIME, args(world))
    await run_as(api_db, world["co_expert"], TIME, args(world, who=world["co_expert"]))
    await run_as(api_db, world["expert"], TIME, args(world))
    count = "select count(*) from app.time_entries"
    assert await run_as(api_db, world["expert"], count) == [(2,)]
    assert await run_as(api_db, world["co_expert"], count) == [(1,)]
    assert await run_as(api_db, world["outsider"], count) == [(0,)]


async def send(api_db: Database, person: uuid.UUID, invoice: uuid.UUID) -> list[tuple[object, ...]]:
    return await run_as(
        api_db,
        person,
        "update app.invoices set status = 'sent' where id = %(i)s returning number, sent_at is not null",
        {"i": invoice},
    )


async def test_invoices_are_numbered_when_sent_without_gaps(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    first, second, third = (one(admin, INVOICE, args(world)) for _ in range(3))
    assert admin.execute("select number from app.invoices where id = %s", (first,)).fetchone() == (None,)
    assert await send(api_db, world["expert"], second) == [(1, True)]
    with pytest.raises(psycopg.errors.DivisionByZero):
        async with api_db.request(scope_for(world["expert"])) as conn:
            await conn.execute("update app.invoices set status = 'sent' where id = %s", (first,))
            await conn.execute("select 1/0")
    assert await send(api_db, world["expert"], third) == [(2, True)]
    assert await send(api_db, world["expert"], first) == [(3, True)]


async def test_only_the_named_expert_sends_an_invoice(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    invoice = one(admin, INVOICE, args(world))
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await send(api_db, world["assistant"], invoice)
    assert await send(api_db, world["co_expert"], invoice) == []
    assert await send(api_db, world["expert"], invoice) == [(1, True)]


async def test_a_sent_invoice_never_changes_except_its_payment_state(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    invoice = one(admin, INVOICE, args(world))
    await send(api_db, world["expert"], invoice)
    for change in ("total = 1", "lines = '[{}]'", "currency = 'XXX'", "number = 9", "status = 'draft'"):
        with refused():
            admin.execute(f"update app.invoices set {change} where id = %s", (invoice,))  # noqa: S608
    admin.execute("update app.invoices set status = 'part_paid', paid_amount = 40 where id = %s", (invoice,))
    admin.execute("update app.invoices set status = 'paid', paid_amount = 100 where id = %s", (invoice,))
    with refused():
        admin.execute("update app.invoices set status = 'written_off' where id = %s", (invoice,))


async def test_a_credit_note_corrects_a_sent_invoice_with_its_own_number(
    admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    invoice = one(admin, INVOICE, args(world))
    note = (
        "insert into app.credit_notes (case_id, invoice_id, amount, reason) values (%s, %s, %s, 'overcharged')"
        " returning number"
    )
    with refused():
        admin.execute(note, (world["case"], invoice, 10))
    admin.execute("update app.invoices set status = 'sent' where id = %s", (invoice,))
    assert admin.execute(note, (world["case"], invoice, 10)).fetchone() == (1,)
    assert admin.execute(note, (world["case"], invoice, 20)).fetchone() == (2,)
    with refused():
        admin.execute(note, (world["case"], invoice, 80))


async def test_a_billing_profile_is_seen_only_by_its_owner(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    count = "select count(*) from app.billing_profiles"
    assert await run_as(api_db, world["expert"], count) == [(1,)]
    assert await run_as(api_db, world["outsider"], count) == [(0,)]
