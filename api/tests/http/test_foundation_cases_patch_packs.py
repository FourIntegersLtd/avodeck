"""PATCH /v1/cases/{case_id} checks pack values against the case's own packs, as creating does (rule 11).

`party_role`, `instructing_party_type` and `court_track` must be keys the case's packs offer;
anything else is 422 `invalid` naming the field, and nothing changes.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi import FastAPI

from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    return {"expert": expert, "case": seed.case(org, expert)}


@pytest.mark.parametrize("field", ["party_role", "instructing_party_type", "court_track"])
async def test_a_value_the_cases_packs_do_not_offer_is_refused(
    field: str, client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(team["expert"])
    resp = await client.patch(f"/v1/cases/{team['case']}", json={field: "not-offered"}, headers={"if-match": '"1"'})
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "invalid"
    assert resp.json()["error"]["field"] == field
    row = admin.execute(f"select {field}, version from app.cases where id = %s", (team["case"],)).fetchone()
    assert row == (None, 1)


async def test_values_the_cases_packs_offer_are_saved(
    app: FastAPI, client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    # The zz-test jurisdiction offers no court tracks, so only these two have a value it accepts.
    assert app.state.packs.jurisdiction_summary("zz-test")["court_tracks"] == []
    change = {"party_role": "instructing_side", "instructing_party_type": "insurer"}
    resp = await (await client_for(team["expert"])).patch(
        f"/v1/cases/{team['case']}", json=change, headers={"if-match": '"1"'}
    )
    assert resp.status_code == 200, resp.text
    assert {k: resp.json()[k] for k in change} == change
