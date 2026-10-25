# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Rule 4: the server decides which records count, so the browser only renders.

- `GET /v1/organisations/{id}` carries `blocking_cases`: the cases the person keeps as named expert if they leave.
- `GET .../members` carries `current_members` and `former_members`; `GET .../invites` carries `pending_invites`.
- `GET /v1/cases/{id}/support-access` carries `active` (one grant or null) and `ended`.
- `GET /v1/support/cases` carries `served_case_ids`.
Every older field stays (contracts only grow).
"""

from __future__ import annotations

import pytest

from tests.fixtures.test_organisations_world import World, world  # noqa: F401
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def test_organisation_names_the_cases_that_stay_with_me_when_i_leave(client_for: ClientFor, world: World) -> None:
    mine = world.team_case("dr_k")
    world.team_case("dr_m")
    world.team_case("dr_m", members={"dr_k": "assistant"})
    world.solo_case()
    resp = await (await client_for(world["dr_k"])).get(f"/v1/organisations/{world.agency}")
    assert resp.status_code == 200, resp.text
    blocking = resp.json()["blocking_cases"]
    assert [b["case_id"] for b in blocking] == [str(mine)]
    assert set(blocking[0]) == {"case_id", "label"}
    other = await (await client_for(world["dr_m"])).get(f"/v1/organisations/{world.agency}")
    assert len(other.json()["blocking_cases"]) == 2
    nobody = await (await client_for(world["viewer"])).get(f"/v1/organisations/{world.agency}")
    assert nobody.json()["blocking_cases"] == []


async def test_members_come_back_split_into_current_and_former(client_for: ClientFor, world: World) -> None:
    world.seed.end_membership(world.agency, world["viewer"])
    body = (await (await client_for(world["owner"])).get(f"/v1/organisations/{world.agency}/members")).json()
    assert str(world["viewer"]) not in {m["person_id"] for m in body["current_members"]}
    assert {m["person_id"] for m in body["former_members"]} == {str(world["viewer"])}
    assert all(m["ended_at"] is None for m in body["current_members"])
    assert len(body["members"]) == len(body["current_members"]) + 1


async def test_invites_come_back_with_the_pending_ones_apart(client_for: ClientFor, world: World) -> None:
    client = await client_for(world["owner"])
    url = f"/v1/organisations/{world.agency}/invites"
    keep = (await client.post(url, json={"email": "keep@example.test", "role": "viewer"})).json()
    gone = (await client.post(url, json={"email": "gone@example.test", "role": "viewer"})).json()
    assert (await client.delete(f"{url}/{gone['id']}")).status_code == 204
    body = (await client.get(url)).json()
    assert [i["id"] for i in body["pending_invites"]] == [keep["id"]]
    assert {i["id"] for i in body["invites"]} == {keep["id"], gone["id"]}


async def test_support_access_returns_the_active_grant_and_the_ended_ones(client_for: ClientFor, world: World) -> None:
    case = world.team_case()
    url = f"/v1/cases/{case}/support-access"
    client = await client_for(world["dr_k"])
    empty = (await client.get(url)).json()
    assert (empty["active"], empty["ended"]) == (None, [])
    first = (await client.post(url, json={"reason": "upload_stuck"})).json()
    second = (await client.post(url, json={"reason": "page_missing"})).json()
    body = (await client.get(url)).json()
    assert body["active"]["id"] == second["id"]
    assert [g["id"] for g in body["ended"]] == [first["id"]]
    assert {g["id"] for g in body["grants"]} == {first["id"], second["id"]}


async def test_support_search_names_the_served_cases(client_for: ClientFor, world: World) -> None:
    served = world.team_case(status="served")
    world.team_case()
    staff = world.seed.person(full_name="Support")
    world.seed.support_staff(staff)
    client = await client_for(staff)
    body = (await client.get("/v1/support/cases", params={"q": world.email("dr_k")})).json()
    assert len(body["cases"]) == 2
    assert body["served_case_ids"] == [str(served)]
    empty = (await client.get("/v1/support/cases", params={"q": "nobody@example.test"})).json()
    assert empty["served_case_ids"] == []
