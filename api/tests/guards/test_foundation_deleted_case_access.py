"""A deleted case (the tombstone `app.purge_case` leaves) answers exactly one action (rule 9).

- `case.view_certificate` still passes on a deleted case, for the same people as before: the named
  expert and organisation owners/admins. Slice H's certificate of deletion is read this way.
- Every other action on a deleted case is 404 for everyone, and so is the certificate for anyone
  else (a case member, another organisation member, an outsider): never 403, so nobody learns more
  about a deleted case than about one that never existed.
- The database (`app.can_access_case`, `app.case_access`) and `check_case_access` agree.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.auth.access import check_case_access
from app.core.auth.permissions import CASE_ACTIONS, CASE_ROLES
from app.core.db import Database
from app.core.errors import ApiError
from tests.support.db import scope_for
from tests.support.seed import Seed

CERTIFICATE = "case.view_certificate"
MAY_SEE_CERTIFICATE = ("named_expert", "owner", "admin")
EVERYONE = (*CASE_ROLES, "owner", "admin", "org_expert", "outsider")

pytestmark = pytest.mark.anyio


@pytest.fixture
def people(seed: Seed) -> dict[str, uuid.UUID]:
    """A deleted team case with someone in every case role, owner/admin/expert in the organisation, an outsider."""
    p = {name: seed.person() for name in EVERYONE}
    org_roles = {"named_expert": "expert", "co_expert": "expert", "supervisor": "expert", "assistant": "assistant",
                 "viewer": "viewer", "owner": "owner", "admin": "admin", "org_expert": "expert"}
    team = seed.org(kind="team", members={p[n]: r for n, r in org_roles.items()})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(
        team, p["named_expert"], status="deleted", members={p[r]: r for r in CASE_ROLES if r != "named_expert"}
    )
    return p


@pytest.mark.parametrize("who", EVERYONE)
async def test_only_the_certificate_passes_on_a_deleted_case(
    api_db: Database, people: dict[str, uuid.UUID], who: str
) -> None:
    case = people["case"]
    for action in sorted(CASE_ACTIONS):
        want = action == CERTIFICATE and who in MAY_SEE_CERTIFICATE
        async with api_db.request(scope_for(people[who])) as conn:
            cur = await conn.execute("select app.can_access_case(%s, %s)", (case, action))
            assert (await cur.fetchone()) == (want,), (who, action, "database")
            if want:
                access = await check_case_access(conn, case, action)
                assert access.allowed_actions == (CERTIFICATE,), who
            else:
                with pytest.raises(ApiError) as err:
                    await check_case_access(conn, case, action)
                assert (err.value.status, err.value.code) == (404, "not_found"), (who, action)


async def test_the_case_list_lookup_still_hides_a_deleted_case(api_db: Database, people: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(people["named_expert"])) as conn:
        cur = await conn.execute("select * from app.case_access(%s)", (people["case"],))
        assert await cur.fetchall() == []


async def test_the_certificate_needs_a_signed_in_person(api_dsn: str, people: dict[str, uuid.UUID]) -> None:
    async with await psycopg.AsyncConnection.connect(api_dsn) as conn:
        cur = await conn.execute("select app.can_access_case(%s, %s)", (people["case"], CERTIFICATE))
        assert await cur.fetchone() == (False,)


async def test_a_live_case_still_answers_every_action(api_db: Database, seed: Seed) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    async with api_db.request(scope_for(expert)) as conn:
        access = await check_case_access(conn, case, CERTIFICATE)
    assert {"case.view_metadata", "fact.verify", CERTIFICATE} <= set(access.allowed_actions)
