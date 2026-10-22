"""Rule 9, scenario S17 and failure mode F38: Avodeck support looks at a case only under a live grant.

The case role `support` applies only while the person is in `app.support_staff` (not removed) and
the case has a `support_access_grants` row from its named expert that is not revoked and has not
expired (checked on every read, so a grant ends by itself after 24 hours). Under it support takes
only the three read actions, every request is written to the case's audit trail as
`support_access.viewed`, and every call needs MFA in the last 10 minutes (`aal2`). The database
answers the same through `app.can_access_case()`, so row-level security repeats it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from app.core.auth.access import check_case_access, require_case_access
from app.core.auth.permissions import CASE_ACTIONS
from app.core.auth.session import Session
from app.core.auth.support import require_support_staff
from app.core.db import Database
from app.core.errors import ApiError
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

READS = ("case.view_metadata", "case.view_content", "case.view_audit")
WRITES = sorted(set(CASE_ACTIONS) - set(READS))


@dataclass
class Desk:
    expert: uuid.UUID
    staff: uuid.UUID
    case: uuid.UUID
    other_case: uuid.UUID


@pytest.fixture
def desk(seed: Seed) -> Desk:
    expert, staff = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    seed.support_staff(staff)
    return Desk(expert, staff, seed.case(org, expert), seed.case(org, expert))


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


async def test_staff_without_a_grant_see_nothing(api_db: Database, desk: Desk) -> None:
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_a_live_grant_opens_the_case_for_reading_only(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    for action in READS:
        assert await answer(api_db, desk.staff, desk.case, action) == (200, True), action
    for action in WRITES:
        assert await answer(api_db, desk.staff, desk.case, action) == (403, False), action


async def test_a_grant_for_any_staff_member_opens_the_case(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=None)
    assert await answer(api_db, desk.staff, desk.case, "case.view_content") == (200, True)


async def test_a_grant_opens_only_its_own_case(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    await assert_sees_nothing(api_db, desk.staff, desk.other_case)


async def test_an_expired_grant_opens_nothing(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff, expires_in=timedelta(minutes=-1))
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_expiry_is_checked_on_every_read(
    api_db: Database, seed: Seed, admin: psycopg.Connection, desk: Desk
) -> None:
    grant = seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    assert await answer(api_db, desk.staff, desk.case, "case.view_content") == (200, True)
    admin.execute(
        "update app.support_access_grants set expires_at = now() - interval '1 second' where id = %s", (grant,)
    )
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_a_revoked_grant_opens_nothing(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff, revoked=True)
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_someone_no_longer_staff_sees_nothing(api_db: Database, seed: Seed, desk: Desk) -> None:
    former = seed.person()
    seed.support_staff(former, removed=True)
    seed.support_grant(desk.case, desk.expert, granted_to=None)
    await assert_sees_nothing(api_db, former, desk.case)


async def test_a_grant_does_not_make_someone_staff(api_db: Database, seed: Seed, desk: Desk) -> None:
    stranger = seed.person()
    seed.support_grant(desk.case, desk.expert, granted_to=stranger)
    await assert_sees_nothing(api_db, stranger, desk.case)


async def test_a_grant_for_another_staff_member_does_not_open_it(api_db: Database, seed: Seed, desk: Desk) -> None:
    colleague = seed.person()
    seed.support_staff(colleague)
    seed.support_grant(desk.case, desk.expert, granted_to=colleague)
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_only_the_named_experts_grant_counts(api_db: Database, seed: Seed, desk: Desk) -> None:
    """A grant row someone else wrote (or one from before the case was reassigned) opens nothing."""
    seed.support_grant(desk.case, seed.person(), granted_to=desk.staff)
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_a_grant_on_a_deleted_case_opens_nothing(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    seed.case_status(desk.case, "deleted")
    await assert_sees_nothing(api_db, desk.staff, desk.case)


async def test_every_view_under_the_support_role_is_in_the_case_audit_trail(
    api_db: Database, seed: Seed, admin: psycopg.Connection, desk: Desk
) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    for action in ("case.view_content", "case.view_content", "case.view_audit"):
        assert (await answer(api_db, desk.staff, desk.case, action))[0] == 200
    await answer(api_db, desk.expert, desk.case, "case.view_content")  # the expert's own views are not support views
    await answer(api_db, desk.staff, desk.case, "fact.verify")  # refused: nothing seen
    rows = admin.execute(
        "select person_id, target from app.audit_log where case_id = %s and action = 'support_access.viewed'"
        " order by seq",
        (desk.case,),
    ).fetchall()
    assert rows == [(desk.staff, f"case:{desk.case}")] * 3


# --- MFA in the last 10 minutes on every support call -------------------------------------------------


def _request(person: uuid.UUID, *, mfa_age: timedelta) -> Any:
    now = datetime.now(UTC)
    session = Session(
        id=uuid.uuid4(),
        person_id=person,
        email="staff@example.test",
        full_name=None,
        csrf_hash="0" * 64,
        aal="aal2",
        mfa_enrolled=True,
        mfa_verified_at=now - mfa_age,
        last_seen_at=now,
        auth_access_token=None,
        auth_refresh_token=None,
        auth_expires_at=None,
    )
    return SimpleNamespace(state=SimpleNamespace(session=session))


async def test_case_routes_under_the_support_role_need_a_fresh_mfa(api_db: Database, seed: Seed, desk: Desk) -> None:
    seed.support_grant(desk.case, desk.expert, granted_to=desk.staff)
    dependency = require_case_access("case.view_content")
    async with api_db.request(scope_for(desk.staff)) as conn:
        access = await dependency(_request(desk.staff, mfa_age=timedelta(minutes=1)), desk.case, conn)
        assert access.case_role == "support"
        with pytest.raises(ApiError) as err:
            await dependency(_request(desk.staff, mfa_age=timedelta(minutes=11)), desk.case, conn)
    assert (err.value.status, err.value.code) == (403, "step_up_required")
    async with api_db.request(scope_for(desk.expert)) as conn:  # the case's own people are not asked
        await dependency(_request(desk.expert, mfa_age=timedelta(hours=3)), desk.case, conn)


async def test_the_support_console_needs_current_staff_and_a_fresh_mfa(api_db: Database, seed: Seed) -> None:
    staff, former, stranger = seed.person(), seed.person(), seed.person()
    seed.support_staff(staff)
    seed.support_staff(former, removed=True)
    assert require_support_staff.step_up is True  # the route scanners see it
    async with api_db.request(scope_for(staff)) as conn:
        found = await require_support_staff(_request(staff, mfa_age=timedelta(minutes=2)), conn)
        assert found.person_id == staff
        with pytest.raises(ApiError) as stale:
            await require_support_staff(_request(staff, mfa_age=timedelta(minutes=11)), conn)
    assert (stale.value.status, stale.value.code) == (403, "step_up_required")
    for person in (former, stranger):
        async with api_db.request(scope_for(person)) as conn:
            with pytest.raises(ApiError) as missing:
                await require_support_staff(_request(person, mfa_age=timedelta(0)), conn)
        assert missing.value.status == 404
