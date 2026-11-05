"""At most three open event streams per person (scalability PR 12), with and without a case notifier.

A fourth is 429 `too_many_streams` with `Retry-After`; a stream that ends (by itself, or because the browser left)
frees its place. The case stream and a job's stream share the one cap. A person with no access to the case gets
404 before any count is revealed (rule 9).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.notify import CaseNotifier
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    app = build_app(api_db, auth_env)
    app.state.job_poll_seconds = 0.05
    app.state.job_stream_seconds = 1.5
    app.state.api_rate_per_minute = 0  # these tests are about streams only
    return app


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "viewer", "stranger")}
    org = seed.org(kind="team", members={p["expert"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["viewer"]: "viewer"})
    p["job"] = seed.job(p["case"])
    return p


def _events(team: dict[str, uuid.UUID]) -> str:
    return f"/v1/cases/{team['case']}/events"


async def _open(client: httpx.AsyncClient, path: str, n: int) -> list[asyncio.Task[httpx.Response]]:
    tasks = [asyncio.create_task(client.get(path)) for _ in range(n)]
    await asyncio.sleep(0.4)
    return tasks


async def test_a_fourth_stream_is_429_too_many_streams_with_retry_after(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    client = await client_for(team["expert"])
    open_streams = await _open(client, _events(team), 3)
    assert not any(t.done() for t in open_streams)
    resp = await client.get(_events(team))
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "too_many_streams"
    assert int(resp.headers["retry-after"]) >= 1
    await asyncio.gather(*open_streams)


async def test_a_stream_that_ends_frees_a_place(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    client = await client_for(team["expert"])
    open_streams = await _open(client, _events(team), 3)
    resps = await asyncio.gather(*open_streams)  # they end by themselves after job_stream_seconds
    assert {r.status_code for r in resps} == {200}
    again = asyncio.create_task(client.get(_events(team)))
    await asyncio.sleep(0.3)
    assert not again.done(), "a fourth stream was let in once the others had ended"
    assert (await again).status_code == 200


async def test_a_browser_that_leaves_frees_its_place(
    client_for: ClientFor, team: dict[str, uuid.UUID], app: FastAPI
) -> None:
    app.state.job_stream_seconds = 30.0
    client = await client_for(team["expert"])
    open_streams = await _open(client, _events(team), 3)
    open_streams[0].cancel()
    await asyncio.sleep(0.3)
    fourth = asyncio.create_task(client.get(_events(team)))
    await asyncio.sleep(0.3)
    assert not fourth.done(), "the place was not freed"
    for t in (*open_streams[1:], fourth):
        t.cancel()
    await asyncio.gather(*open_streams, fourth, return_exceptions=True)


async def test_another_person_is_unaffected(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    expert = await client_for(team["expert"])
    viewer = await client_for(team["viewer"])
    open_streams = await _open(expert, _events(team), 3)
    other = asyncio.create_task(viewer.get(_events(team)))
    await asyncio.sleep(0.3)
    assert not other.done()
    await asyncio.gather(*open_streams, other)


async def test_a_job_stream_counts_against_the_same_cap(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    client = await client_for(team["expert"])
    open_streams = await _open(client, _events(team), 2)
    open_streams += await _open(client, f"/v1/jobs/{team['job']}/events", 1)
    resp = await client.get(_events(team))
    assert resp.status_code == 429
    assert resp.json()["error"]["code"] == "too_many_streams"
    await asyncio.gather(*open_streams)


async def test_no_access_is_404_not_429_even_with_every_place_taken(
    client_for: ClientFor, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    org = seed.org(kind="team", members={team["stranger"]: "owner"})
    mine = seed.case(org, team["stranger"])
    stranger = await client_for(team["stranger"])
    open_streams = await _open(stranger, f"/v1/cases/{mine}/events", 3)
    # Rule 9: access is checked before the cap, so a 429 never tells a stranger a case exists.
    resp = await stranger.get(_events(team))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    job = await stranger.get(f"/v1/jobs/{team['job']}/events")
    assert job.status_code == 404
    await asyncio.gather(*open_streams)


@pytest.fixture
async def notifier(api_dsn: str) -> AsyncIterator[CaseNotifier]:
    n = CaseNotifier(api_dsn, backoff_min=0.05, backoff_max=0.2, ping_seconds=5)
    await n.start()
    await n.wait_ready(10)
    yield n
    await n.stop()


async def test_the_cap_holds_when_streams_are_pushed_by_a_notifier(
    client_for: ClientFor, team: dict[str, uuid.UUID], app: FastAPI, notifier: CaseNotifier
) -> None:
    app.state.case_notifier = notifier
    client = await client_for(team["expert"])
    open_streams = await _open(client, _events(team), 3)
    resp = await client.get(_events(team))
    assert resp.status_code == 429
    resps = await asyncio.gather(*open_streams)
    assert {r.status_code for r in resps} == {200}
    again = asyncio.create_task(client.get(_events(team)))
    await asyncio.sleep(0.3)
    assert not again.done()
    assert (await again).status_code == 200


async def test_a_limit_of_zero_is_no_limit(client_for: ClientFor, team: dict[str, uuid.UUID], app: FastAPI) -> None:
    app.state.api_max_streams_per_person = 0
    client = await client_for(team["expert"])
    open_streams = await _open(client, _events(team), 5)
    assert not any(t.done() for t in open_streams)
    await asyncio.gather(*open_streams)
