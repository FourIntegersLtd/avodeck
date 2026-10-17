"""The duplicate-case check leaves out the case being set up (slice C review finding 10).

On screen 04 the subject key is saved on the draft case before the check runs; passing that case's id
keeps it out of the matches, so the expert is only told about other cases. Without `case_id` every case
the person can see is compared, as before.
"""

# ruff: noqa: F811  (the shared fixtures are imported from the case tests)
from __future__ import annotations

import uuid

import pytest

from tests.http.test_setup_case_routes import IDENTIFIER, app, packs, world  # noqa: F401
from tests.support.db import ClientFor
from tests.support.packs import FIXTURE
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_the_case_being_set_up_is_not_its_own_duplicate(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["expert"])
    other = seed.case(world["org"], world["expert"], status="active")
    for case in (world["case"], other):
        saved = await client.put(f"/v1/cases/{case}/subject-key", json={"identifier": IDENTIFIER})
        assert saved.status_code == 204, saved.text
    check = {"jurisdiction": FIXTURE.jurisdiction, "identifier": IDENTIFIER}

    everything = (await client.post("/v1/cases/duplicate-check", json=check)).json()["matches"]
    assert {m["case_id"] for m in everything} == {str(world["case"]), str(other)}
    others = await client.post("/v1/cases/duplicate-check", json={**check, "case_id": str(world["case"])})
    assert others.status_code == 200, others.text
    assert [m["case_id"] for m in others.json()["matches"]] == [str(other)]
