"""GET /v1/cases/{case_id}/events: one live stream of a case's background jobs (architecture 5.3).

Every wait in the app is a job on a case, so one stream per open case tells every screen when its job
moves on. It carries only status, step, counts and an error code (rule 6). Someone who cannot see the
case gets 404 (rule 9), and an open stream is not activity: it must not keep the 30-minute idle
sign-out from firing (`touch=False`).
"""

from __future__ import annotations

import asyncio
import json
import uuid

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

JOB_FIELDS = {"id", "kind", "status", "step", "pct", "eta_seconds", "step_done", "step_total", "error_code"}


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    app = build_app(api_db, auth_env)
    app.state.job_poll_seconds = 0.05
    app.state.job_stream_seconds = 0.8
    return app


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "viewer", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "owner"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["viewer"]: "viewer"})
    p["job"] = seed.job(p["case"])
    return p


def events(resp: httpx.Response) -> list[dict[str, list[dict[str, object]]]]:
    return [json.loads(line[len("data: ") :]) for line in resp.text.splitlines() if line.startswith("data: ")]


async def test_a_case_member_gets_the_cases_jobs_with_progress_fields_only(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    admin.execute(
        "update app.jobs set status = 'running', step = 'ocr', pct = 30, step_done = 275, step_total = 882"
        " where id = %s",
        (team["job"],),
    )
    resp = await (await client_for(team["viewer"])).get(f"/v1/cases/{team['case']}/events")

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-store"
    first = events(resp)[0]
    assert set(first) == {"jobs"}
    (job,) = first["jobs"]
    assert set(job) == JOB_FIELDS
    assert (job["id"], job["kind"], job["status"], job["step"], job["step_done"], job["step_total"]) == (
        str(team["job"]),
        "process_bundle",
        "running",
        "ocr",
        275,
        882,
    )


async def test_someone_without_access_gets_404(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    for path in (f"/v1/cases/{team['case']}/events", f"/v1/cases/{uuid.uuid4()}/events"):
        resp = await (await client_for(team["outsider"])).get(path)
        assert resp.status_code == 404, (path, resp.text)
        assert resp.json()["error"]["code"] == "not_found"


async def test_another_cases_jobs_never_appear(client_for: ClientFor, team: dict[str, uuid.UUID], seed: Seed) -> None:
    org = seed.org(kind="team", members={team["outsider"]: "owner"})
    other_case = seed.case(org, team["outsider"])
    other_job = seed.job(other_case)
    resp = await (await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/events")
    assert str(other_job) not in resp.text
    assert str(team["job"]) in resp.text


async def test_a_new_event_is_sent_when_a_job_changes_and_not_otherwise(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, app: FastAPI
) -> None:
    app.state.job_stream_seconds = 2.0
    admin.execute(
        "update app.jobs set status = 'running', step = 'ocr', step_done = 0, step_total = 882 where id = %s",
        (team["job"],),
    )
    client = await client_for(team["expert"])
    stream = asyncio.create_task(client.get(f"/v1/cases/{team['case']}/events"))
    for done in (100, 275):
        await asyncio.sleep(0.3)
        admin.execute("update app.jobs set step_done = %s where id = %s", (done, team["job"]))
    resp = await asyncio.wait_for(stream, 10)

    counts = [e["jobs"][0]["step_done"] for e in events(resp)]
    assert counts[:3] == [0, 100, 275], counts
    assert all(a != b for a, b in zip(counts, counts[1:], strict=False)), "only changes are sent"


async def test_a_job_finished_long_ago_is_left_out_and_a_new_job_appears(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, seed: Seed, app: FastAPI
) -> None:
    old = seed.job(team["case"], kind="rerun_checks")
    admin.execute("update app.jobs set status = 'running' where id = %s", (old,))
    admin.execute("update app.jobs set status = 'done', finished_at = now() - interval '2 hours' where id = %s", (old,))
    recent = seed.job(team["case"], kind="find_may_matter")
    admin.execute("update app.jobs set status = 'running' where id = %s", (recent,))
    admin.execute("update app.jobs set status = 'done', finished_at = now() where id = %s", (recent,))
    resp = await (await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/events")
    ids = {j["id"] for j in events(resp)[0]["jobs"]}
    assert str(old) not in ids
    assert {str(team["job"]), str(recent)} <= ids


async def test_the_stream_ends_after_its_time_limit_and_sends_a_heartbeat_when_quiet(
    client_for: ClientFor, team: dict[str, uuid.UUID], app: FastAPI
) -> None:
    app.state.job_heartbeat_seconds = 0.05
    resp = await asyncio.wait_for((await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/events"), 10)
    assert resp.status_code == 200
    assert ": keepalive" in resp.text
    assert len(events(resp)) == 1, "a heartbeat is a comment, not an event"


async def test_losing_access_ends_the_stream(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, app: FastAPI
) -> None:
    app.state.job_stream_seconds = 5.0
    client = await client_for(team["viewer"])
    stream = asyncio.create_task(client.get(f"/v1/cases/{team['case']}/events"))
    await asyncio.sleep(0.3)
    admin.execute("delete from app.case_members where case_id = %s and person_id = %s", (team["case"], team["viewer"]))
    resp = await asyncio.wait_for(stream, 3)  # ends well before the 5 s limit
    assert resp.status_code == 200


async def test_an_open_stream_is_not_activity_for_the_idle_sign_out(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    admin.execute(
        "update app.sessions set last_seen_at = now() - interval '10 minutes' where person_id = %s", (team["expert"],)
    )
    client = await client_for(team["expert"])
    admin.execute(
        "update app.sessions set last_seen_at = now() - interval '10 minutes' where person_id = %s", (team["expert"],)
    )
    await client.get(f"/v1/cases/{team['case']}/events")
    row = admin.execute(
        "select extract(epoch from now() - last_seen_at) / 60 from app.sessions where person_id = %s",
        (team["expert"],),
    ).fetchone()
    assert row is not None
    minutes = row[0]
    assert minutes >= 9.9, "the stream must not count as activity"


async def test_the_job_stream_is_not_activity_either(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection, app: FastAPI
) -> None:
    client = await client_for(team["expert"])
    admin.execute(
        "update app.sessions set last_seen_at = now() - interval '10 minutes' where person_id = %s", (team["expert"],)
    )
    admin.execute("update app.jobs set status = 'running' where id = %s", (team["job"],))
    admin.execute("update app.jobs set status = 'done' where id = %s", (team["job"],))
    await client.get(f"/v1/jobs/{team['job']}/events")
    row = admin.execute(
        "select extract(epoch from now() - last_seen_at) / 60 from app.sessions where person_id = %s",
        (team["expert"],),
    ).fetchone()
    assert row is not None
    minutes = row[0]
    assert minutes >= 9.9
