"""Trial packs and offline copies are built for a hearing and go stale when new material arrives after they
were built (architecture 10.7; state-machines.md "Trial pack and offline package freshness"; S10, F35).

- Packs and copies may be bound to one of the case's scheduled hearings (slice G's), never another case's.
- Adjourning or changing that hearing, adding pages to the records, or serving anything newer marks the
  newest pack and every active copy stale, with a short reason; a re-issued court bundle does the same.
- Making the pack (or a copy) again gives a current one. Slice G's timeline reads the pack the same way.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from app.core.contracts.trial_steps import latest_pack_step
from app.core.db import Database
from tests.fixtures.trial.fixtures import (  # noqa: F401
    aconn,
    app,
    combo,
    reads,
    run_job,
    storage,
    upload_bundle,
    worker_db,
    world,
)
from tests.fixtures.trial.world import World, serve_report, trial_bundle
from tests.support.db import ClientFor, scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def hearing(admin: psycopg.Connection, case: uuid.UUID, starts_on: str = "2027-03-01") -> uuid.UUID:
    row = admin.execute(
        "insert into app.hearings (case_id, type, starts_on, ends_on) values (%s, 'zz_trial', %s, %s) returning id",
        (case, starts_on, starts_on),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def adjourn(admin: psycopg.Connection, case: uuid.UUID, old: uuid.UUID) -> uuid.UUID:
    new = hearing(admin, case, "2027-09-01")
    admin.execute("update app.hearings set status = 'adjourned', replaced_by_hearing_id = %s where id = %s", (new, old))
    return new


async def for_hearing(client_for: ClientFor, w: World, hearing_id: uuid.UUID | None) -> Any:
    return await (await client_for(w.expert)).put(
        w.url("trial-pack/settings"),
        json={"sections": ["report"], "hearing_id": str(hearing_id) if hearing_id else None},
        headers={"If-Match": '"0"'},
    )


async def build(client_for: ClientFor, worker_db: Database, w: World) -> None:
    resp = await (await client_for(w.expert)).post(w.url("trial-pack"))
    assert resp.status_code == 202, resp.text
    await run_job(worker_db, w, "build_trial_pack", resp.json()["job_id"])


async def pack(client_for: ClientFor, w: World) -> dict[str, Any]:
    resp = await (await client_for(w.viewer)).get(w.url("trial-pack/latest"))
    assert resp.status_code == 200, resp.text
    out: dict[str, Any] = resp.json()["pack"]
    return out


async def copy(client_for: ClientFor, w: World, hearing_id: uuid.UUID | None = None) -> dict[str, Any]:
    body: dict[str, Any] = {"device_label": "Zz tablet"}
    if hearing_id:
        body["hearing_id"] = str(hearing_id)
    resp = await (await client_for(w.expert)).post(w.url("offline-packages"), json=body)
    assert resp.status_code == 201, resp.text
    made: dict[str, Any] = resp.json()
    assert made["stale"] is False and made["stale_reason"] is None
    return made


async def copies(client_for: ClientFor, w: World) -> dict[str, dict[str, Any]]:
    resp = await (await client_for(w.viewer)).get(w.url("offline-packages"))
    assert resp.status_code == 200, resp.text
    return {p["id"]: p for p in resp.json()["packages"]}


async def both_built(
    client_for: ClientFor, worker_db: Database, w: World, hearing_id: uuid.UUID | None
) -> dict[str, Any]:
    assert (await for_hearing(client_for, w, hearing_id)).status_code == 200
    await build(client_for, worker_db, w)
    built = await pack(client_for, w)
    assert built["stale"] is False and built["stale_reason"] is None
    return await copy(client_for, w, hearing_id)


async def assert_stale(client_for: ClientFor, w: World, made: dict[str, Any], reason: str) -> None:
    latest = await pack(client_for, w)
    assert (latest["stale"], latest["stale_reason"]) == (True, reason)
    listed = (await copies(client_for, w))[made["id"]]
    assert (listed["stale"], listed["stale_reason"]) == (True, reason)
    one = (await (await client_for(w.viewer)).get(w.url(f"offline-packages/{made['id']}"))).json()
    assert (one["stale"], one["stale_reason"]) == (True, reason)


async def test_packs_are_bound_only_to_a_scheduled_hearing_of_the_case(
    client_for: ClientFor, world: World, admin: psycopg.Connection, seed: Seed
) -> None:
    other_expert = seed.person()
    other_case = seed.case(seed.org(kind="personal", members={other_expert: "owner"}), other_expert, status="active")
    theirs = hearing(admin, other_case)
    assert (await for_hearing(client_for, world, theirs)).status_code == 422
    ours = hearing(admin, world.case)
    adjourn(admin, world.case, ours)
    assert (await for_hearing(client_for, world, ours)).status_code == 422
    refused = await (await client_for(world.expert)).post(
        world.url("offline-packages"), json={"device_label": "Zz tablet", "hearing_id": str(ours)}
    )
    assert refused.status_code == 422
    current = hearing(admin, world.case, "2027-10-01")
    saved = await for_hearing(client_for, world, current)
    assert saved.status_code == 200 and saved.json()["hearing_id"] == str(current)


async def test_adjourning_the_hearing_marks_the_pack_and_the_copy_stale_s10(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection, api_db: Database
) -> None:
    trial = hearing(admin, world.case)
    made = await both_built(client_for, worker_db, world, trial)
    async with api_db.request(scope_for(world.viewer)) as conn:
        step = await latest_pack_step(conn, world.case)
    assert step is not None and step.stale is False
    adjourn(admin, world.case, trial)
    await assert_stale(client_for, world, made, "hearing_adjourned")
    async with api_db.request(scope_for(world.viewer)) as conn:
        step = await latest_pack_step(conn, world.case)
    assert step is not None and step.stale is True


async def test_a_moved_hearing_date_marks_them_stale(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    trial = hearing(admin, world.case)
    made = await both_built(client_for, worker_db, world, trial)
    admin.execute("update app.hearings set starts_on = '2027-03-08', ends_on = '2027-03-09' where id = %s", (trial,))
    await assert_stale(client_for, world, made, "hearing_changed")


async def test_new_pages_in_the_records_mark_them_stale(
    client_for: ClientFor, world: World, worker_db: Database, seed: Seed
) -> None:
    made = await both_built(client_for, worker_db, world, None)
    seed.page(world.case, world.document, 300)
    await assert_stale(client_for, world, made, "new_pages")


async def test_a_newer_served_version_marks_them_stale(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
) -> None:
    made = await both_built(client_for, worker_db, world, None)
    await serve_report(admin, aconn, world, version_no=2)
    await assert_stale(client_for, world, made, "new_served_version")


async def test_making_them_again_gives_a_current_pack_and_copy(
    client_for: ClientFor, world: World, worker_db: Database, seed: Seed
) -> None:
    made = await both_built(client_for, worker_db, world, None)
    seed.page(world.case, world.document, 300)
    await assert_stale(client_for, world, made, "new_pages")
    await build(client_for, worker_db, world)
    again = await pack(client_for, world)
    assert again["stale"] is False and again["stale_reason"] is None
    fresh = await copy(client_for, world)
    assert (await copies(client_for, world))[fresh["id"]]["stale"] is False


async def test_a_reissued_court_bundle_marks_the_copy_stale_too_f35(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    client = await client_for(world.expert)
    first = await upload_bundle(client, worker_db, world, trial_bundle()[0])
    made = await both_built(client_for, worker_db, world, None)
    resp = await client.post(
        world.url(f"court-bundles/{first['court_bundle_id']}/supersede"),
        content=trial_bundle(removed=[], rescanned=[])[0],
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 202, resp.text
    await assert_stale(client_for, world, made, "court_bundle_superseded")
    row = admin.execute(
        "select stale, stale_reason, stale_at is not null from app.offline_packages where id = %s", (made["id"],)
    ).fetchone()
    assert row == (True, "court_bundle_superseded", True)
