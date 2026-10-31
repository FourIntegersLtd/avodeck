"""GET /v1/jobs/{job_id} and its server-sent events stream (architecture 5.3, screen 07).

Progress carries only status, step, percentage, time left and an error code: never patient text.
Someone who cannot see the job's case gets 404, as for any case resource.
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

PROGRESS_FIELDS = {"status", "step", "pct", "eta_seconds", "error_code"}


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    app = build_app(api_db, auth_env)
    app.state.job_poll_seconds = 0.05
    app.state.job_stream_seconds = 10.0
    return app


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "viewer", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "owner"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["viewer"]: "viewer"})
    p["job"] = seed.job(p["case"])
    return p


def events(resp: httpx.Response) -> list[dict[str, object]]:
    return [json.loads(line[len("data: ") :]) for line in resp.text.splitlines() if line.startswith("data: ")]


async def test_a_case_member_reads_a_jobs_progress(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    admin.execute(
        "update app.jobs set status = 'running', pct = 30, step = 'ocr', eta_seconds = 90 where id = %s", (team["job"],)
    )
    resp = await (await client_for(team["viewer"])).get(f"/v1/jobs/{team['job']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(team["job"])
    assert body["case_id"] == str(team["case"])
    assert (body["kind"], body["status"], body["step"], body["pct"], body["eta_seconds"]) == (
        "process_bundle",
        "running",
        "ocr",
        30,
        90,
    )
    assert body["error_code"] is None


async def test_someone_without_access_gets_404_for_the_job_and_its_stream(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    outsider = await client_for(team["outsider"])
    for path in (f"/v1/jobs/{team['job']}", f"/v1/jobs/{team['job']}/events", f"/v1/jobs/{uuid.uuid4()}"):
        resp = await outsider.get(path)
        assert resp.status_code == 404, (path, resp.text)
        assert resp.json()["error"]["code"] == "not_found"


async def test_progress_streams_over_sse_until_the_job_finishes_and_carries_no_patient_text(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    admin.execute("update app.jobs set status = 'running' where id = %s", (team["job"],))
    client = await client_for(team["expert"])
    stream = asyncio.create_task(client.get(f"/v1/jobs/{team['job']}/events"))
    await asyncio.sleep(0.3)
    admin.execute("update app.jobs set pct = 50, step = 'ocr', eta_seconds = 40 where id = %s", (team["job"],))
    await asyncio.sleep(0.3)
    admin.execute("update app.jobs set status = 'done', pct = 100, eta_seconds = 0 where id = %s", (team["job"],))
    resp = await asyncio.wait_for(stream, 10)

    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert resp.headers["cache-control"] == "no-store"
    sent = events(resp)
    assert sent, "no progress events"
    for event in sent:
        assert set(event) == PROGRESS_FIELDS, event
    assert {"status": "running", "step": "ocr", "pct": 50, "eta_seconds": 40, "error_code": None} in sent
    assert sent[-1]["status"] == "done"
    assert sent[0]["status"] == "running"
    # Only changes are sent, and nothing about the case or its files.
    assert all(a != b for a, b in zip(sent, sent[1:], strict=False))
    assert "Case Z" not in resp.text and "f.pdf" not in resp.text


async def test_a_stream_ends_after_its_time_limit_so_the_browser_reconnects(
    app: FastAPI, client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    app.state.job_stream_seconds = 0.3
    admin.execute("update app.jobs set status = 'running' where id = %s", (team["job"],))
    resp = await asyncio.wait_for((await client_for(team["expert"])).get(f"/v1/jobs/{team['job']}/events"), 10)
    assert resp.status_code == 200
    assert events(resp)[-1]["status"] == "running"


async def test_stream_limit_is_under_the_proxy_timeout(api_db: Database, auth_env: dict[str, str]) -> None:
    """Vercel cuts a proxied request at 120 s as an error; the stream must end itself first, cleanly."""
    from app.core.routes.jobs import JOB_STREAM_SECONDS, PROXY_TIMEOUT_SECONDS

    assert JOB_STREAM_SECONDS < PROXY_TIMEOUT_SECONDS
    assert build_app(api_db, auth_env).state.job_stream_seconds == JOB_STREAM_SECONDS
