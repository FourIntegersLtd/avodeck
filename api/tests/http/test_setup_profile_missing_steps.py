"""Rule 4: the profile reply names the onboarding steps still to do; the screen does not work them out."""

# ruff: noqa: F811  (the shared fixtures are imported from the profile tests)
from __future__ import annotations

import uuid

import pytest

from tests.http.test_setup_profile_routes import app, cell_region, expert, storage  # noqa: F401
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio

ALL = ["about", "roles", "training", "template", "signature"]


async def test_a_new_profile_misses_every_step_and_each_one_done_drops_out(
    client_for: ClientFor, expert: uuid.UUID
) -> None:
    client = await client_for(expert)
    first = await client.get("/v1/me/profile")
    assert first.json()["missing_steps"] == ALL

    saved = await client.patch(
        "/v1/me/profile",
        json={"full_name": "Dr Test", "registration_number": "1234567", "specialties": ["zz-specialty"]},
        headers={"if-match": first.headers["etag"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["missing_steps"] == ALL[1:]

    role = {"specialty": "zz-specialty", "role_title": "Practitioner", "started_on": "2020-01-01"}
    assert (await client.post("/v1/me/roles", json=role)).status_code == 201
    assert (await client.get("/v1/me/profile")).json()["missing_steps"] == ALL[2:]
    assert (await client.post("/v1/me/training", json={"title": "Course", "year": 2019})).status_code == 201
    assert (await client.get("/v1/me/profile")).json()["missing_steps"] == ALL[3:]
