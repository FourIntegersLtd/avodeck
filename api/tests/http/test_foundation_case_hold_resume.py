"""Resuming a case releases what its hold kept back (slice C's `POST /cases/{id}/resume`).

The route calls `app.core.case_hold.release_case_hold()`: the held messages are listed and emailed,
and the held jobs go back on the queue.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.notifications import notify
from tests.support.db import ClientFor, scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_resuming_releases_held_messages_and_jobs(
    app: FastAPI, api_db: Database, client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    expert, assistant = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant"})
    client = await client_for(expert)
    assert (await client.post(f"/v1/cases/{case}/hold")).status_code == 200
    async with api_db.request(scope_for(expert)) as conn:
        await notify(conn, app.state.mailer, person_id=assistant, organisation_id=org, kind="zz.ping", case_id=case)
    job = uuid.uuid4()
    admin.execute(
        "insert into app.jobs (id, case_id, kind, status, error_code) values (%s, %s, 'zz_work', 'queued',"
        " 'case_on_hold')",
        (job, case),
    )
    helper = await client_for(assistant)
    assert (await helper.get("/v1/notifications")).json()["items"] == []
    sent = len(app.state.mailer.outbox)

    resp = await client.post(f"/v1/cases/{case}/resume")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"on_hold": False}
    assert [i["kind"] for i in (await helper.get("/v1/notifications")).json()["items"]] == ["zz.ping"]
    assert len(app.state.mailer.outbox) == sent + 1
    assert admin.execute("select error_code from app.jobs where id = %s", (job,)).fetchone() == (None,)
    queued = admin.execute(
        "select count(*) from procrastinate.procrastinate_jobs where args ->> 'job_id' = %s and status = 'todo'",
        (str(job),),
    ).fetchone()
    assert queued == (1,)
