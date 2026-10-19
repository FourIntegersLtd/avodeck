"""Slice G: withdrawing a case cancels its open deadlines, and slice C's response says how many.

Slice C publishes `CaseWithdrawn` in its transaction; G cancels every open deadline of the case (kept, with
`cancelled_at`) so no reminder goes out, and adds the count. Deadlines already done are left alone.
"""

# ruff: noqa: F811
from __future__ import annotations

import psycopg
import pytest

from tests.fixtures.test_after_serving_world import (  # noqa: F401
    World,
    aconn,
    app,
    combo,
    mailer,
    make_version,
    model,
    ocr,
    reads,
    served,
    services,
    storage,
    world,
)
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def test_withdrawing_cancels_the_open_deadlines(
    client_for: ClientFor, admin: psycopg.Connection, served: World
) -> None:
    expert = await client_for(served.expert)
    assert (await expert.get(served.url("deadlines"))).status_code == 200  # the pack's deadline is made
    added = await expert.post(served.url("deadlines"), json={"kind": "zz_other_kind", "due_on": "2026-05-04"})
    assert added.status_code == 201, added.text
    done = await expert.post(served.url("deadlines"), json={"kind": "zz_done_kind", "due_on": "2026-05-05"})
    admin.execute("update app.deadlines set done_at = now() where id = %s", (done.json()["id"],))
    open_now = admin.execute(
        "select count(*) from app.deadlines where case_id = %s and done_at is null", (served.case,)
    ).fetchone()
    assert open_now is not None and open_now[0] >= 2

    resp = await expert.post(served.url("withdraw"), json={"reason": "I have been discharged."})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deadlines_cancelled"] == open_now[0]

    rows = admin.execute(
        "select done_at is not null, cancelled_at is not null from app.deadlines where case_id = %s", (served.case,)
    ).fetchall()
    assert sorted(rows) == sorted([(True, False)] + [(False, True)] * open_now[0])
    listed = (await expert.get(served.url("deadlines"))).json()["deadlines"]
    assert all(d["cancelled_at"] for d in listed if d["done_at"] is None)


async def test_a_case_with_no_deadlines_cancels_none(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world.expert)).post(world.url("withdraw"), json={"reason": "Conflict found."})
    assert resp.status_code == 200, resp.text
    assert resp.json()["deadlines_cancelled"] == 0
