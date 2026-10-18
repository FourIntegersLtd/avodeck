"""The job and the case's processing status carry `step_done` and `step_total` (migration 0028).

Both are whole numbers or null: "page 40 of 900" on screen 07 without any page text (rule 6).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {"expert": seed.person()}
    org = seed.org(kind="personal", members={p["expert"]: "owner"})
    p["case"] = seed.case(org, p["expert"])
    p["job"] = seed.job(p["case"])
    return p


async def test_the_job_returns_its_counts(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(team["expert"])
    body = (await client.get(f"/v1/jobs/{team['job']}")).json()
    assert (body["step_done"], body["step_total"]) == (None, None)
    admin.execute(
        "update app.jobs set status = 'running', step_done = 40, step_total = 900 where id = %s", (team["job"],)
    )
    body = (await client.get(f"/v1/jobs/{team['job']}")).json()
    assert (body["step_done"], body["step_total"]) == (40, 900)


async def test_the_processing_status_returns_the_counts(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    admin.execute(
        "update app.jobs set status = 'running', step_done = 7, step_total = 12 where id = %s", (team["job"],)
    )
    resp = await (await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/process")
    assert resp.status_code == 200, resp.text
    job = resp.json()["job"]
    assert (job["step_done"], job["step_total"]) == (7, 12)
