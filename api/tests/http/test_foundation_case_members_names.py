"""`GET /v1/cases/{id}/members` gives each member's display name (from `contracts.members`)."""

from __future__ import annotations

import pytest

from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_the_members_list_carries_display_names(client_for: ClientFor, seed: Seed) -> None:
    expert, assistant = seed.person(full_name="Zed Expert"), seed.person(full_name="Zed Helper")
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant"})
    case = seed.case(org, expert, members={assistant: "assistant"})
    resp = await (await client_for(assistant)).get(f"/v1/cases/{case}/members")
    assert resp.status_code == 200, resp.text
    assert [(m["person_id"], m["display_name"]) for m in resp.json()] == [(str(assistant), "Zed Helper")]
