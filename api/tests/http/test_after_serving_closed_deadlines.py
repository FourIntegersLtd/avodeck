"""Deadlines on a closed case, and cancelled deadlines (state-machines.md, Deadline).

A withdrawn, settled or archived case gets no new pack deadlines: not from reading the list, not from a
court direction, and not from the database function called directly. A cancelled deadline is terminal: it
cannot be moved or marked done or undone (409 `cancelled`; the database refuses too), and a court
direction never picks it up.
"""

# ruff: noqa: F811
from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from app.core.http import etag
from tests.fixtures.test_after_serving_world import (  # noqa: F401
    World,
    aconn,
    app,
    combo,
    mailer,
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

# The path from served to each closed status (state-machines.md, Case).
CLOSED = {"withdrawn": ["withdrawn"], "settled": ["settled"], "archived": ["settled", "archived"]}


def close(admin: psycopg.Connection, w: World, status: str) -> None:
    for step in CLOSED[status]:
        admin.execute("update app.cases set status = %s where id = %s", (step, w.case))


def count(admin: psycopg.Connection, w: World) -> int:
    row = admin.execute("select count(*) from app.deadlines where case_id = %s", (w.case,)).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("status", list(CLOSED))
async def test_a_closed_case_gets_no_new_pack_deadlines(
    client_for: ClientFor, admin: psycopg.Connection, api_dsn: str, served: World, status: str
) -> None:
    close(admin, served, status)
    expert = await client_for(served.expert)
    listed = await expert.get(served.url("deadlines"))
    assert listed.status_code == 200, listed.text
    assert listed.json()["deadlines"] == []
    assert count(admin, served) == 0, "reading the list makes nothing"

    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(served.expert),))
        conn.execute("select set_config('app.org_ids', %s, true)", (str(served.org),))
        row = conn.execute(
            "select app.after_serving_pack_deadline(%s, 'zz_kind', 'zz.rule', %s, 'zz', 'Europe/London', 'v1')",
            (served.case, date(2026, 6, 1)),
        ).fetchone()
    assert row == (None,), "the database makes no pack deadline on a closed case"
    assert count(admin, served) == 0


async def cancelled_deadline(client_for: ClientFor, admin: psycopg.Connection, w: World) -> dict[str, object]:
    expert = await client_for(w.expert)
    made = await expert.post(w.url("deadlines"), json={"kind": "zz_other_kind", "due_on": "2026-05-04"})
    assert made.status_code == 201, made.text
    admin.execute("update app.deadlines set cancelled_at = now() where id = %s", (made.json()["id"],))
    listed = (await expert.get(w.url("deadlines"))).json()["deadlines"]
    return next(d for d in listed if d["id"] == made.json()["id"])


async def test_a_cancelled_deadline_cannot_be_moved_or_marked_done(
    client_for: ClientFor, admin: psycopg.Connection, served: World
) -> None:
    deadline = await cancelled_deadline(client_for, admin, served)
    expert = await client_for(served.expert)
    url = served.url(f"deadlines/{deadline['id']}")
    headers = {"If-Match": etag(int(str(deadline["version"])))}
    for body in ({"due_on": "2026-06-01", "reason": "moved"}, {"done": True}, {"done": False}):
        resp = await expert.patch(url, json=body, headers=headers)
        assert resp.status_code == 409 and resp.json()["error"]["code"] == "cancelled", (body, resp.text)
    row = admin.execute("select due_on, done_at from app.deadlines where id = %s", (deadline["id"],)).fetchone()
    assert row == (date(2026, 5, 4), None)
    with pytest.raises(psycopg.Error, match="cancelled"):
        admin.execute("update app.deadlines set done_at = now() where id = %s", (deadline["id"],))


async def test_a_court_direction_never_picks_a_cancelled_deadline(
    client_for: ClientFor, admin: psycopg.Connection, served: World
) -> None:
    deadline = await cancelled_deadline(client_for, admin, served)
    direction = {"made_on": "2026-04-01", "deadlines": [{"kind": "zz_other_kind", "due_on": "2026-07-01"}]}
    resp = await (await client_for(served.expert)).post(served.url("court-directions"), json=direction)
    assert resp.status_code == 201, resp.text
    created = [uuid.UUID(x) for x in resp.json()["created_deadline_ids"]]
    assert len(created) == 1 and str(created[0]) != deadline["id"], "a new open deadline, not the cancelled one"
    row = admin.execute("select due_on, cancelled_at is not null from app.deadlines where id = %s", (deadline["id"],))
    assert row.fetchone() == (date(2026, 5, 4), True)
