"""Today (screen 03): slice C's "needs you" items (slice C brief).

- Questions to confirm: on each case where the person is the named expert and the latest letter's
  questions are read but not yet confirmed, with how many are waiting; it opens screen 06 and goes once
  the set is confirmed. Only the named expert confirms, so an assistant does not see it.
- Profile incomplete: once (on one case), while the expert's profile lacks a name, profession,
  registration body or number, or a specialty; it opens screen 02 and goes once the profile is filled in.

Items carry i18n keys and counts only (rule 6).
"""

# ruff: noqa: F811  (the shared fixtures are imported from the questions tests)
from __future__ import annotations

import uuid
from typing import Any

import pytest

from app.core.ai.bedrock import RecordedBedrock
from app.core.db import Database
from tests.http.test_setup_questions_routes import (  # noqa: F401
    app,
    crypto,
    model,
    read_letter,
    storage,
    url,
    worker_db,
    world,
)
from tests.support.db import ClientFor
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SPECIALTY = LAUNCH.case_row()["specialty"]
FULL_PROFILE = {
    "full_name": "Dr A Expert",
    "profession": "doctor",
    "specialties": [SPECIALTY],
    "registration_body": "A register",
    "registration_number": "1234567",
}


async def items(client: Any, kind: str) -> list[dict[str, Any]]:
    got = await client.get("/v1/today")
    assert got.status_code == 200, got.status_code
    return [i for i in got.json()["needs_you"] if i["kind"] == kind]


async def test_questions_to_confirm_until_the_set_is_confirmed(
    client_for: ClientFor, world: dict[str, uuid.UUID], worker_db: Database, model: RecordedBedrock
) -> None:
    client = await client_for(world["expert"])
    assert await items(client, "setup_questions_to_confirm") == []
    await read_letter(client, world, worker_db)

    [item] = await items(client, "setup_questions_to_confirm")
    assert item["case_id"] == str(world["case"])
    assert item["screen"] == "S06" and item["count"] == 4
    assert item["label_key"].startswith("setup.") and item["action_key"].startswith("setup.")
    assert await items(await client_for(world["assistant"]), "setup_questions_to_confirm") == []

    assert (await client.post(url(world, "questions/confirm-all"))).status_code == 200
    assert await items(client, "setup_questions_to_confirm") == []


async def test_profile_incomplete_shows_once_until_the_profile_is_filled_in(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["expert"])
    org = seed.org(kind="personal", members={world["expert"]: "owner"})
    seed.case(org, world["expert"], status="active", packs=LAUNCH.case_row())

    [item] = await items(client, "setup_profile_incomplete")
    assert item["screen"] == "S02" and item["count"] and item["count"] > 0
    assert item["label_key"].startswith("setup.") and item["action_key"].startswith("setup.")
    assert await items(await client_for(world["assistant"]), "setup_profile_incomplete") == []

    tag = (await client.get("/v1/me/profile")).headers["etag"]
    saved = await client.patch("/v1/me/profile", json=FULL_PROFILE, headers={"if-match": tag})
    assert saved.status_code == 200, saved.status_code
    assert await items(client, "setup_profile_incomplete") == []
