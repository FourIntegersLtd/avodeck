"""Messages, message choices and feature flags (architecture 5.2, 10.9; permissions "Person actions").

- `GET /v1/notifications`: the person's own in-app messages across all organisations, newest first,
  `?unread=true` for unread only, the unread count for the bell, and `email_bounced` for the banner (F33).
- `POST /v1/notifications/{id}/read`: marks one read (the first time is kept); anyone else's is 404.
- `GET/PUT /v1/me/notification-preferences`: per organisation, in the app / by email / one email a
  day and muted kinds; only the person's own current organisations.
- `GET /v1/feature-flags`: flags that apply to this person, worked out on the server: a person flag
  beats an organisation flag, which beats the cell's, which beats the global one.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi import Depends, FastAPI

from app.core.flags import require_flag
from app.core.http import router
from app.main import mount
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    w = {n: seed.person() for n in ("me", "other")}
    w["personal"] = seed.org(kind="personal", members={w["me"]: "owner"})
    w["team"] = seed.org(kind="team", members={w["me"]: "expert", w["other"]: "owner"})
    w["left"] = seed.org(kind="team", members={w["me"]: "expert"})
    seed.end_membership(w["left"], w["me"])
    w["case"] = seed.case(w["personal"], w["me"])
    return w


# --- messages -----------------------------------------------------------------------------------


async def test_signed_out_is_401(client_for: ClientFor) -> None:
    client = await client_for(None)
    assert (await client.get("/v1/notifications")).status_code == 401
    assert (await client.get("/v1/feature-flags")).status_code == 401
    assert (await client.get("/v1/me/notification-preferences")).status_code == 401


async def test_lists_only_my_messages_newest_first_with_the_unread_count(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    first = seed.notification(world["me"], world["personal"], world["case"])
    second = seed.notification(world["me"], world["team"])
    hidden = seed.notification(world["me"], world["team"])
    seed.notification(world["other"], world["team"])
    admin.execute("update app.notifications set created_at = now() - interval '1 hour' where id = %s", (first,))
    admin.execute("update app.notifications set in_app = false where id = %s", (hidden,))
    admin.execute("update app.notifications set read_at = now() where id = %s", (first,))
    resp = await (await client_for(world["me"])).get("/v1/notifications")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [n["id"] for n in body["items"]] == [str(second), str(first)]
    assert set(body["items"][0]) >= {"id", "kind", "organisation_id", "case_id", "case_label", "created_at", "read_at"}
    assert body["items"][1]["case_label"] == "Case Z"
    assert body["unread_count"] == 1
    assert body["email_bounced"] is False
    unread = (await (await client_for(world["me"])).get("/v1/notifications", params={"unread": "true"})).json()
    assert [n["id"] for n in unread["items"]] == [str(second)]


async def test_a_bounced_address_is_reported_for_the_banner(
    client_for: ClientFor, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute("update app.people set email_bounced_at = now() where id = %s", (world["me"],))
    body = (await (await client_for(world["me"])).get("/v1/notifications")).json()
    assert body["email_bounced"] is True


async def test_marking_read_keeps_the_first_time_and_hides_other_peoples_messages(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    mine = seed.notification(world["me"], world["personal"])
    theirs = seed.notification(world["other"], world["team"])
    client = await client_for(world["me"])
    resp = await client.post(f"/v1/notifications/{mine}/read")
    assert resp.status_code == 200, resp.text
    first = resp.json()["read_at"]
    assert first is not None
    again = await client.post(f"/v1/notifications/{mine}/read")
    assert again.json()["read_at"] == first
    assert (await client.post(f"/v1/notifications/{theirs}/read")).status_code == 404
    assert (await client.post(f"/v1/notifications/{uuid.uuid4()}/read")).status_code == 404
    row = admin.execute("select read_at from app.notifications where id = %s", (theirs,)).fetchone()
    assert row == (None,)


# --- message choices ----------------------------------------------------------------------------


async def test_choices_default_to_app_and_email_for_each_current_organisation(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    resp = await (await client_for(world["me"])).get("/v1/me/notification-preferences")
    assert resp.status_code == 200, resp.text
    found = {p["organisation_id"]: p for p in resp.json()}
    assert set(found) == {str(world["personal"]), str(world["team"])}, "not the organisation I left"
    personal = found[str(world["personal"])]
    assert (personal["in_app"], personal["email"], personal["digest"], personal["muted_kinds"]) == (
        True, True, False, [],
    )
    assert personal["organisation_kind"] == "personal" and found[str(world["team"])]["organisation_name"]


async def test_changing_choices_saves_them_per_organisation(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["me"])
    change = {"organisation_id": str(world["team"]), "in_app": True, "email": False, "digest": True,
              "muted_kinds": ["zz.ready"]}
    resp = await client.put("/v1/me/notification-preferences", json=[change])
    assert resp.status_code == 200, resp.text
    found = {p["organisation_id"]: p for p in (await client.get("/v1/me/notification-preferences")).json()}
    team = found[str(world["team"])]
    assert (team["in_app"], team["email"], team["digest"], team["muted_kinds"]) == (True, False, True, ["zz.ready"])
    assert found[str(world["personal"])]["email"] is True
    change["muted_kinds"] = []
    await client.put("/v1/me/notification-preferences", json=[change])
    found = {p["organisation_id"]: p for p in (await client.get("/v1/me/notification-preferences")).json()}
    assert found[str(world["team"])]["muted_kinds"] == []


async def test_choices_only_for_my_own_current_organisations(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["me"])
    for org in (world["left"], seed.org(kind="team")):
        body = [{"organisation_id": str(org), "in_app": True, "email": True, "digest": False, "muted_kinds": []}]
        assert (await client.put("/v1/me/notification-preferences", json=body)).status_code == 404


async def test_muted_kinds_are_short_codes(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    body = [{"organisation_id": str(world["personal"]), "in_app": True, "email": True, "digest": False,
             "muted_kinds": ["Not a code!"]}]
    assert (await (await client_for(world["me"])).put("/v1/me/notification-preferences", json=body)).status_code == 422


# --- feature flags ------------------------------------------------------------------------------


async def test_flags_are_worked_out_on_the_server_most_specific_first(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    key = f"zz_{uuid.uuid4().hex[:8]}"
    seed.flag(f"{key}_global", "global")
    seed.flag(f"{key}_cell", "global", enabled=True)
    seed.flag(f"{key}_cell", "cell", enabled=False)
    seed.flag(f"{key}_org", "cell", enabled=False)
    seed.flag(f"{key}_org", "organisation", world["team"], enabled=True)
    seed.flag(f"{key}_person", "organisation", world["team"], enabled=True)
    seed.flag(f"{key}_person", "person", world["me"], enabled=False)
    seed.flag(f"{key}_theirs", "person", world["other"], enabled=True)
    seed.flag(f"{key}_left", "organisation", world["left"], enabled=True)
    resp = await (await client_for(world["me"])).get("/v1/feature-flags")
    assert resp.status_code == 200, resp.text
    flags = {k: v for k, v in resp.json()["flags"].items() if k.startswith(key)}
    assert flags == {
        f"{key}_global": True,
        f"{key}_cell": False,
        f"{key}_org": True,
        f"{key}_person": False,
    }


async def test_a_route_behind_a_flag_is_404_until_the_flag_is_on(
    app: FastAPI, client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    key = f"zz_{uuid.uuid4().hex[:8]}"
    flagged = router(prefix="/v1/zz-flagged", tags=["test"])

    @flagged.get("", dependencies=[Depends(require_flag(key))])
    async def zz_flagged() -> dict[str, bool]:
        return {"ok": True}

    mount(app, flagged)
    client = await client_for(world["me"])
    assert (await client.get("/v1/zz-flagged")).status_code == 404
    seed.flag(key, "person", world["me"])
    assert (await client.get("/v1/zz-flagged")).status_code == 200
