"""Rule 9 and architecture 10.1 "Death or incapacity": a records custodian only reads, and only served cases.

When the named expert can no longer act, Avodeck support records a custody transfer
(`custody_transfers`, migration 0031; slice O's route sits behind the flag `support_custody_transfer`)
to a person, or to an organisation whose current owners and admins then hold the records. The case
role `custodian` applies while the transfer is not revoked, the case has been served, and the
person's account is open; it takes only the read actions (`CUSTODIAN_ACTIONS`), never a write.
Anyone with a case role of their own keeps it. The database answers the same through
`app.can_access_case()`, so row-level security repeats it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg
import pytest

from app.core.auth.access import check_case_access
from app.core.auth.permissions import CASE_ACTIONS, CUSTODIAN_ACTIONS, CUSTODIAN_ROLE, allowed
from app.core.db import Database
from app.core.errors import ApiError
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

READS = ("case.view_metadata", "case.view_content", "case.view_audit", "case.view_certificate")
WRITES = sorted(set(CASE_ACTIONS) - set(READS))


@dataclass
class Estate:
    expert: uuid.UUID
    staff: uuid.UUID
    custodian: uuid.UUID
    org: uuid.UUID
    served: uuid.UUID
    unserved: uuid.UUID


@pytest.fixture
def estate(seed: Seed) -> Estate:
    expert, staff, custodian = seed.person(), seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    seed.support_staff(staff)
    return Estate(
        expert, staff, custodian, org, seed.case(org, expert, status="served"), seed.case(org, expert, status="active")
    )


async def answer(api_db: Database, person: uuid.UUID, case: uuid.UUID, action: str) -> tuple[int, bool]:
    """(HTTP-like status from require_case_access, what the database says) for one action."""
    async with api_db.request(scope_for(person)) as conn:
        cur = await conn.execute("select app.can_access_case(%s, %s)", (case, action))
        row = await cur.fetchone()
        assert row is not None
        try:
            await check_case_access(conn, case, action)
        except ApiError as err:
            return err.status, row[0]
    return 200, row[0]


async def assert_sees_nothing(api_db: Database, person: uuid.UUID, case: uuid.UUID) -> None:
    for action in sorted(CASE_ACTIONS):
        assert await answer(api_db, person, case, action) == (404, False), action


async def assert_reads_only(api_db: Database, person: uuid.UUID, case: uuid.UUID) -> None:
    for action in READS:
        assert await answer(api_db, person, case, action) == (200, True), action
    for action in WRITES:
        assert await answer(api_db, person, case, action) == (403, False), action


def test_a_custodian_never_writes() -> None:
    assert CUSTODIAN_ROLE == "custodian"
    assert frozenset(READS) == CUSTODIAN_ACTIONS
    assert {a for a in CASE_ACTIONS if allowed(a, CUSTODIAN_ROLE, None, "team")} == CUSTODIAN_ACTIONS
    assert all(CUSTODIAN_ROLE not in rule.case_roles for rule in CASE_ACTIONS.values())  # not a case_members role


async def test_without_a_transfer_the_custodian_sees_nothing(api_db: Database, estate: Estate) -> None:
    await assert_sees_nothing(api_db, estate.custodian, estate.served)


async def test_a_transfer_to_a_person_opens_the_served_case_for_reading_only(
    api_db: Database, seed: Seed, estate: Estate
) -> None:
    seed.custody_transfer(estate.served, estate.staff, to_person=estate.custodian)
    await assert_reads_only(api_db, estate.custodian, estate.served)


async def test_a_transfer_opens_only_its_own_case(api_db: Database, seed: Seed, estate: Estate) -> None:
    seed.custody_transfer(estate.served, estate.staff, to_person=estate.custodian)
    other = seed.case(estate.org, estate.expert, status="served")
    await assert_sees_nothing(api_db, estate.custodian, other)


async def test_a_case_never_served_cannot_be_transferred(admin: psycopg.Connection, seed: Seed, estate: Estate) -> None:
    with pytest.raises(psycopg.Error) as err:
        seed.custody_transfer(estate.unserved, estate.staff, to_person=estate.custodian)
    assert err.value.sqlstate == "AV409"


async def test_a_revoked_transfer_opens_nothing(api_db: Database, seed: Seed, estate: Estate) -> None:
    seed.custody_transfer(estate.served, estate.staff, to_person=estate.custodian, revoked=True)
    await assert_sees_nothing(api_db, estate.custodian, estate.served)


async def test_a_transfer_to_an_organisation_opens_the_case_to_its_owners_and_admins(
    api_db: Database, seed: Seed, estate: Estate
) -> None:
    owner, admin_, expert, former = seed.person(), seed.person(), seed.person(), seed.person()
    keeper = seed.org(kind="team", members={owner: "owner", admin_: "admin", expert: "expert", former: "admin"})
    seed.end_membership(keeper, former)
    seed.custody_transfer(estate.served, estate.staff, to_organisation=keeper)
    for who in (owner, admin_):
        await assert_reads_only(api_db, who, estate.served)
    for who in (expert, former):
        await assert_sees_nothing(api_db, who, estate.served)


async def test_a_closed_account_holds_no_custody(api_db: Database, seed: Seed, estate: Estate) -> None:
    seed.custody_transfer(estate.served, estate.staff, to_person=estate.custodian)
    seed.close_account(estate.custodian)
    await assert_sees_nothing(api_db, estate.custodian, estate.served)


async def test_someone_on_the_case_keeps_their_own_role(api_db: Database, seed: Seed, estate: Estate) -> None:
    """The named expert, should custody pass to them, stays the named expert."""
    seed.custody_transfer(estate.served, estate.staff, to_person=estate.expert)
    async with api_db.request(scope_for(estate.expert)) as conn:
        access = await check_case_access(conn, estate.served, "case.view_metadata")
    assert access.case_role == "named_expert"


# --- the table ------------------------------------------------------------------------------------


def transfer_as(api_dsn: str, person: uuid.UUID, case: uuid.UUID, to: uuid.UUID, by: uuid.UUID) -> uuid.UUID:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        row = conn.execute(
            "insert into app.custody_transfers (case_id, from_person_id, to_person_id, evidence_note, transferred_by)"
            " values (%s, %s, %s, 'Death certificate seen', %s) returning id",
            (case, person, to, by),
        ).fetchone()
        assert row is not None
        return row[0]  # type: ignore[no-any-return]


async def test_only_current_staff_record_a_transfer_in_their_own_name(api_dsn: str, seed: Seed, estate: Estate) -> None:
    for who in (estate.expert, estate.custodian):
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            transfer_as(api_dsn, who, estate.served, estate.custodian, who)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        transfer_as(api_dsn, estate.staff, estate.served, estate.custodian, estate.expert)  # someone else's name
    transfer_as(api_dsn, estate.staff, estate.served, estate.custodian, estate.staff)


async def test_the_database_records_who_it_was_from_and_when(
    api_dsn: str, admin: psycopg.Connection, estate: Estate
) -> None:
    tid = transfer_as(api_dsn, estate.staff, estate.served, estate.custodian, estate.staff)
    row = admin.execute(
        "select from_person_id, transferred_at > now() - interval '1 minute' from app.custody_transfers where id = %s",
        (tid,),
    ).fetchone()
    assert row == (estate.expert, True)


async def test_only_revoking_changes_a_transfer_and_only_once(
    api_dsn: str, seed: Seed, admin: psycopg.Connection, estate: Estate
) -> None:
    tid = seed.custody_transfer(estate.served, estate.staff, to_person=estate.custodian)
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(estate.staff),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("update app.custody_transfers set to_person_id = %s where id = %s", (estate.staff, tid))
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(estate.staff),))
        conn.execute("update app.custody_transfers set revoked_at = now() where id = %s", (tid,))
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.custody_transfers set revoked_at = null where id = %s", (tid,))
    assert err.value.sqlstate == "AV409"


def test_transfers_are_kept_after_the_case_is_deleted() -> None:
    """The record of who holds the records, like the audit trail: ids, dates and a short note about the expert."""
    from app.core.contracts.case_tables import CASE_TABLES, KEPT_AFTER_CASE_DELETION

    assert "custody_transfers" in KEPT_AFTER_CASE_DELETION
    assert "custody_transfers" not in CASE_TABLES
