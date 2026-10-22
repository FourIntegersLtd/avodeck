"""Contracts for slice O: `org_policy` (core/contracts/org_policy.py) and the close contract
(core/contracts/close.py) over slice H's `export_case` and `delete_case` jobs.

- `org_policy(conn, org_id)` reads `organisations.policy`: the template the organisation requires
  and its retention limit in years, each None when not set. Members read it, so does anyone who can
  see one of the organisation's cases (a named expert who left) and the job of such a case.
  Anyone else gets 404. A policy of the wrong shape is refused, never read as "no limit".
- `request_case_deletion(conn, case_id)`: account closure (S15) deletes an unserved case the way
  slice H's "delete now" does: needs `case.delete_now`, refused (409) once served, on hold or
  already being deleted; sets `delete_now`, due today, and queues `delete_case`.
- `case_export_done(conn, case_id, person_id)`: has this person's export of the case finished?
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.core.contracts.close import case_export_done, request_case_deletion
from app.core.contracts.org_policy import OrgPolicy, org_policy
from app.core.db import Database
from app.core.errors import ApiError
from app.core.features import load_features
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module", autouse=True)
def _job_kinds() -> Iterator[None]:
    load_features()  # slice H registers `export_case` and `delete_case` on the app's job registry
    yield


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("owner", "expert", "assistant", "outsider")}
    p["org"] = seed.org(kind="team", members={p["owner"]: "owner", p["expert"]: "expert", p["assistant"]: "assistant"})
    p["case"] = seed.case(p["org"], p["expert"], status="active", members={p["assistant"]: "assistant"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    return p


def set_policy(admin: psycopg.Connection, org: uuid.UUID, policy: object) -> None:
    admin.execute("update app.organisations set policy = %s where id = %s", (Jsonb(policy), org))


# --- org_policy ----------------------------------------------------------------------------------


async def test_no_policy_means_personal_defaults(api_db: Database, team: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(team["owner"])) as conn:
        assert await org_policy(conn, team["org"]) == OrgPolicy(required_template_id=None, max_retention_years=None)


async def test_members_read_the_policy(api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    template = uuid.uuid4()
    set_policy(admin, team["org"], {"required_template_id": str(template), "max_retention_years": 8})
    for who in ("owner", "expert", "assistant"):
        async with api_db.request(scope_for(team[who])) as conn:
            assert await org_policy(conn, team["org"]) == OrgPolicy(template, 8), who


async def test_a_named_expert_who_left_still_reads_it(
    api_db: Database, admin: psycopg.Connection, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    set_policy(admin, team["org"], {"max_retention_years": 6})
    seed.end_membership(team["org"], team["expert"])
    async with api_db.request(scope_for(team["expert"])) as conn:
        assert await org_policy(conn, team["org"]) == OrgPolicy(None, 6)


async def test_the_cases_own_job_reads_it(
    worker_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    set_policy(admin, team["org"], {"max_retention_years": 5})
    worker = Database(worker_dsn, max_size=1)
    await worker.open()
    try:
        async with worker.job(team["case"]) as conn:
            assert await org_policy(conn, team["org"]) == OrgPolicy(None, 5)
        async with worker.job(uuid.uuid4()) as conn:  # another case's job
            with pytest.raises(ApiError):
                await org_policy(conn, team["org"])
    finally:
        await worker.close()


async def test_anyone_else_gets_404(api_db: Database, team: dict[str, uuid.UUID]) -> None:
    for org in (team["org"], uuid.uuid4()):
        async with api_db.request(scope_for(team["outsider"])) as conn:
            with pytest.raises(ApiError) as err:
                await org_policy(conn, org)
        assert err.value.status == 404


@pytest.mark.parametrize(
    "policy",
    [{"max_retention_years": "seven"}, {"max_retention_years": 0}, {"required_template_id": "not-an-id"}, []],
)
async def test_a_policy_of_the_wrong_shape_is_refused(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID], policy: object
) -> None:
    set_policy(admin, team["org"], policy)
    async with api_db.request(scope_for(team["owner"])) as conn:
        with pytest.raises(ValueError):
            await org_policy(conn, team["org"])


# --- close contract ----------------------------------------------------------------------------------


async def test_requesting_deletion_queues_slice_hs_delete_job(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    async with api_db.request(scope_for(team["expert"])) as conn:
        job = await request_case_deletion(conn, team["case"])
    row = admin.execute(
        "select j.kind, j.status, j.requested_by, c.retention_policy, c.delete_after = current_date"
        " from app.jobs j join app.cases c on c.id = j.case_id where j.id = %s",
        (job,),
    ).fetchone()
    assert row == ("delete_case", "queued", team["expert"], "delete_now", True)
    audit = admin.execute(
        "select person_id, target from app.audit_log where case_id = %s and action = 'case.retention_changed'",
        (team["case"],),
    ).fetchall()
    assert audit == [(team["expert"], "retention:delete_now")]


@pytest.mark.parametrize("status", ["served", "settled"])
async def test_a_served_case_is_never_deleted_this_way(api_db: Database, seed: Seed, status: str) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status=status)
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(ApiError) as err:
            await request_case_deletion(conn, case)
    assert err.value.status == 409


async def test_a_case_on_hold_is_not_deleted(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    admin.execute("update app.cases set on_hold = true where id = %s", (team["case"],))
    async with api_db.request(scope_for(team["expert"])) as conn:
        with pytest.raises(ApiError) as err:
            await request_case_deletion(conn, team["case"])
    assert err.value.status == 409


async def test_a_case_under_legal_hold_is_not_deleted(
    api_db: Database, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    admin.execute(
        "insert into app.legal_holds (case_id, reason, set_by) values (%s, 'appeal', %s)", (team["case"], team["owner"])
    )
    async with api_db.request(scope_for(team["expert"])) as conn:
        with pytest.raises(ApiError) as err:
            await request_case_deletion(conn, team["case"])
    assert err.value.status == 409


@pytest.mark.parametrize(("who", "status"), [("assistant", 403), ("outsider", 404)])
async def test_only_those_who_may_delete_now_can_ask(
    api_db: Database, team: dict[str, uuid.UUID], who: str, status: int
) -> None:
    async with api_db.request(scope_for(team[who])) as conn:
        with pytest.raises(ApiError) as err:
            await request_case_deletion(conn, team["case"])
    assert err.value.status == status


async def test_an_export_is_done_only_once_this_persons_export_has_finished(
    api_db: Database, admin: psycopg.Connection, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    async def done(person: uuid.UUID) -> bool:
        async with api_db.request(scope_for(team["expert"])) as conn:
            return await case_export_done(conn, team["case"], person)

    assert await done(team["expert"]) is False
    job = seed.job(team["case"], kind="export_case", requested_by=team["expert"])
    assert await done(team["expert"]) is False  # queued
    for step in ("running", "done"):
        admin.execute("update app.jobs set status = %s where id = %s", (step, job))
    assert await done(team["expert"]) is True
    assert await done(team["owner"]) is False  # someone else's export does not count
    other = seed.job(team["case"], kind="process_bundle", requested_by=team["owner"])
    for step in ("running", "done"):
        admin.execute("update app.jobs set status = %s where id = %s", (step, other))
    assert await done(team["owner"]) is False  # not an export
