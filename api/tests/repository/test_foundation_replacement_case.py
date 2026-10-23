"""Scenario S3 after serving: a replacement expert gets a new case linked `replaces` to the served one.

`core/contracts/replacement.py` `create_replacement_case()` is what slice O's
`POST /v1/cases/{case_id}/replacement` calls (action `case.reassign_named_expert` 🔒: an organisation
owner or admin). Through `app.create_replacement_case()` (migration 0031) it:
- refuses a case that has never been served (409 `not_served`: reassign it instead);
- creates a draft case in the same organisation, under the same packs and label (or a new label),
  named to a current owner, admin or expert member other than the served case's named expert (else 422);
  the person creating it does not join it;
- links it to the served case (`case_links.kind = 'replaces'`, slice C's table) and records whether
  the instructing party consented to the bundle being copied (`case_replacements`, kept after deletion).
It copies nothing from the served case's bundle: copying is slice A's, and only with consent.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg
import pytest

from app.core.contracts.case_tables import CASE_TABLES, KEPT_AFTER_CASE_DELETION
from app.core.contracts.replacement import ReplacementCase, create_replacement_case
from app.core.db import Database
from app.core.errors import ApiError
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@dataclass
class Agency:
    owner: uuid.UUID
    dr_k: uuid.UUID
    dr_m: uuid.UUID
    assistant: uuid.UUID
    org: uuid.UUID
    served: uuid.UUID


@pytest.fixture
def agency(seed: Seed) -> Agency:
    owner, dr_k, dr_m, assistant = seed.person(), seed.person(), seed.person(), seed.person()
    org = seed.org(kind="team", members={owner: "owner", dr_k: "expert", dr_m: "expert", assistant: "assistant"})
    return Agency(owner, dr_k, dr_m, assistant, org, seed.case(org, dr_k, status="served"))


async def replace(
    api_db: Database, person: uuid.UUID, case: uuid.UUID, expert: uuid.UUID, *, consented: bool = True,
    label: str | None = None,
) -> ReplacementCase:  # fmt: skip
    async with api_db.request(scope_for(person)) as conn:
        return await create_replacement_case(
            conn, case, named_expert_id=expert, instructing_party_consented=consented, label=label
        )


async def test_a_replacement_is_a_new_draft_case_for_the_new_expert(
    api_db: Database, admin: psycopg.Connection, agency: Agency
) -> None:
    made = await replace(api_db, agency.owner, agency.served, agency.dr_m)
    assert (made.replaces_case_id, made.named_expert_id, made.instructing_party_consented) == (
        agency.served, agency.dr_m, True,
    )  # fmt: skip
    new = admin.execute(
        "select organisation_id, named_expert_id, status, jurisdiction, case_type, specialty, expert_role, label"
        " from app.cases where id = %s",
        (made.case_id,),
    ).fetchone()
    old = admin.execute(
        "select organisation_id, jurisdiction, case_type, specialty, expert_role, label from app.cases where id = %s",
        (agency.served,),
    ).fetchone()
    assert new is not None and old is not None
    assert new[:3] == (agency.org, agency.dr_m, "draft")
    assert new[3:] == old[1:]


async def test_it_is_linked_replaces_and_records_consent(
    api_db: Database, admin: psycopg.Connection, agency: Agency
) -> None:
    made = await replace(api_db, agency.owner, agency.served, agency.dr_m, consented=False, label="Case Z (2)")
    links = admin.execute(
        "select linked_case_id, kind from app.case_links where case_id = %s", (made.case_id,)
    ).fetchall()
    assert links == [(agency.served, "replaces")]
    consent = admin.execute(
        "select replaces_case_id, instructing_party_consented, recorded_by from app.case_replacements"
        " where case_id = %s",
        (made.case_id,),
    ).fetchone()
    assert consent == (agency.served, False, agency.owner)
    assert admin.execute("select label from app.cases where id = %s", (made.case_id,)).fetchone() == ("Case Z (2)",)


async def test_nothing_from_the_bundle_is_copied_and_the_creator_does_not_join(
    api_db: Database, admin: psycopg.Connection, agency: Agency
) -> None:
    made = await replace(api_db, agency.owner, agency.served, agency.dr_m)
    for table in ("documents", "pages", "facts", "case_members"):
        count = admin.execute(f"select count(*) from app.{table} where case_id = %s", (made.case_id,)).fetchone()  # noqa: S608
        assert count == (0,), table


async def test_the_new_expert_is_its_named_expert(api_db: Database, agency: Agency) -> None:
    made = await replace(api_db, agency.owner, agency.served, agency.dr_m)
    async with api_db.request(scope_for(agency.dr_m)) as conn:
        cur = await conn.execute("select case_role from app.case_access(%s, 'fact.verify')", (made.case_id,))
        assert await cur.fetchone() == ("named_expert",)


async def test_creating_it_is_audited_on_both_cases(
    api_db: Database, admin: psycopg.Connection, agency: Agency
) -> None:
    made = await replace(api_db, agency.owner, agency.served, agency.dr_m)
    rows = admin.execute(
        "select case_id, action from app.audit_log where person_id = %s and case_id in (%s, %s) order by seq",
        (agency.owner, made.case_id, agency.served),
    ).fetchall()
    assert set(rows) == {(made.case_id, "case.created"), (agency.served, "case.replacement_created")}


async def test_a_case_never_served_is_reassigned_not_replaced(api_db: Database, seed: Seed, agency: Agency) -> None:
    unserved = seed.case(agency.org, agency.dr_k, status="active")
    with pytest.raises(ApiError) as err:
        await replace(api_db, agency.owner, unserved, agency.dr_m)
    assert (err.value.status, err.value.code) == (409, "not_served")


@pytest.mark.parametrize("who", ["dr_k", "assistant", "self"])
async def test_the_new_expert_is_another_expert_member(api_db: Database, agency: Agency, who: str) -> None:
    expert = {"dr_k": agency.dr_k, "assistant": agency.assistant, "self": agency.owner}[who]
    if who == "self":  # an owner may name themselves: they are an expert member too
        made = await replace(api_db, agency.owner, agency.served, expert)
        assert made.named_expert_id == agency.owner
        return
    with pytest.raises(ApiError) as err:
        await replace(api_db, agency.owner, agency.served, expert)
    assert (err.value.status, err.value.code) == (422, "invalid")


async def test_only_an_organisation_owner_or_admin_replaces(api_db: Database, agency: Agency) -> None:
    """The database repeats `case.reassign_named_expert`: the served case's own expert cannot (404)."""
    with pytest.raises(ApiError) as err:
        await replace(api_db, agency.dr_k, agency.served, agency.dr_m)
    assert err.value.status == 404


async def test_the_running_app_cannot_write_consent_by_hand(api_dsn: str, agency: Agency) -> None:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(agency.owner),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(
                "insert into app.case_replacements (case_id, replaces_case_id, instructing_party_consented,"
                " recorded_by) values (%s, %s, true, %s)",
                (agency.served, agency.served, agency.owner),
            )


def test_the_record_of_consent_is_kept_after_deletion() -> None:
    assert "case_replacements" in KEPT_AFTER_CASE_DELETION
    assert "case_replacements" not in CASE_TABLES
