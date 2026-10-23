"""An organisation's retention limit (`policy.max_retention_years`, read through core/contracts/org_policy.py)
caps what a team case may choose on screen 26 (cross-slice request O -> H).

- `PUT /retention` with `keep_default` is refused (422 `retention_exceeds_org_policy`) when the pack's
  years are longer than the limit; `return_then_delete` and `delete_now` are never longer, so they pass.
- Settling a case without a chosen policy never dates it later than the limit.
- `GET /close` says the limit (`max_retention_years`, null when the organisation sets none).
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import psycopg
import pytest
from psycopg.types.json import Jsonb

from app.core.packs import default_library
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PACK_YEARS = int(default_library().combine("zz-test", "zz-case", "zz-specialty").retention_years)


def plus_years(day: date, n: int) -> date:
    try:
        return day.replace(year=day.year + n)
    except ValueError:  # 29 February
        return day.replace(year=day.year + n, day=28)


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "owner")}
    p["org"] = seed.org(kind="team", members={p["expert"]: "expert", p["owner"]: "owner"})
    return p


def limit(admin: psycopg.Connection, org: uuid.UUID, years: int | None) -> None:
    policy = {} if years is None else {"max_retention_years": years}
    admin.execute("update app.organisations set policy = %s where id = %s", (Jsonb(policy), org))


def row_of(admin: psycopg.Connection, case: uuid.UUID) -> dict[str, Any]:
    cur = admin.execute("select retention_policy, delete_after, settled_at from app.cases where id = %s", (case,))
    row = cur.fetchone()
    assert row is not None
    return dict(zip(("policy", "delete_after", "settled_at"), row, strict=True))


def test_the_fixture_years_leave_room_for_a_lower_limit() -> None:
    assert PACK_YEARS > 1


async def test_keep_default_longer_than_the_limit_is_refused(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    limit(admin, team["org"], PACK_YEARS - 1)
    case = seed.case(team["org"], team["expert"], status="settled")
    resp = await (await client_for(team["expert"])).put(f"/v1/cases/{case}/retention", json={"policy": "keep_default"})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "retention_exceeds_org_policy"
    assert row_of(admin, case)["policy"] is None


async def test_keep_default_within_the_limit_is_allowed(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    limit(admin, team["org"], PACK_YEARS)
    case = seed.case(team["org"], team["expert"], status="settled")
    resp = await (await client_for(team["expert"])).put(f"/v1/cases/{case}/retention", json={"policy": "keep_default"})
    assert resp.status_code == 200, resp.text
    assert row_of(admin, case)["policy"] == "keep_default"


async def test_shorter_choices_are_allowed_under_any_limit(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    limit(admin, team["org"], 1)
    case = seed.case(team["org"], team["expert"], status="settled")
    resp = await (await client_for(team["expert"])).put(
        f"/v1/cases/{case}/retention", json={"policy": "return_then_delete"}
    )
    assert resp.status_code == 200, resp.text
    other = seed.case(team["org"], team["expert"], status="active")
    resp = await (await client_for(team["expert"])).put(f"/v1/cases/{other}/retention", json={"policy": "delete_now"})
    assert resp.status_code == 200, resp.text


async def test_settling_never_dates_a_case_later_than_the_limit(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    limit(admin, team["org"], PACK_YEARS - 1)
    case = seed.case(team["org"], team["expert"], status="served")
    resp = await (await client_for(team["expert"])).post(f"/v1/cases/{case}/settle")
    assert resp.status_code == 200, resp.text
    row = row_of(admin, case)
    assert row["delete_after"] == plus_years(row["settled_at"].date(), PACK_YEARS - 1)


async def test_the_summary_says_the_limit_or_null(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = seed.case(team["org"], team["expert"], status="active")
    client = await client_for(team["expert"])
    assert (await client.get(f"/v1/cases/{case}/close")).json()["max_retention_years"] is None
    limit(admin, team["org"], 3)
    assert (await client.get(f"/v1/cases/{case}/close")).json()["max_retention_years"] == 3


async def test_a_personal_case_has_no_limit(client_for: ClientFor, seed: Seed) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="settled")
    client = await client_for(expert)
    assert (await client.get(f"/v1/cases/{case}/close")).json()["max_retention_years"] is None
    resp = await client.put(f"/v1/cases/{case}/retention", json={"policy": "keep_default"})
    assert resp.status_code == 200, resp.text
