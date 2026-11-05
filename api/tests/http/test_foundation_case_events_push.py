"""The case stream is pushed, not polled, when a notifier is running (PR 8).

`fetch` calls are counted by wrapping `case_events._jobs`: one at connect, one per notify, one per safety
tick. With no notifier (tests, local) the stream keeps its timed loop, covered by test_foundation_case_events.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.notify import CaseNotifier
from app.core.routes import case_events
from app.core.sse import AccessRecheck
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def notifier(api_dsn: str) -> AsyncIterator[CaseNotifier]:
    n = CaseNotifier(api_dsn, backoff_min=0.05, backoff_max=0.2, ping_seconds=5)
    await n.start()
    await n.wait_ready(10)
    yield n
    await n.stop()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], notifier: CaseNotifier) -> FastAPI:
    app = build_app(api_db, auth_env)
    app.state.case_notifier = notifier
    app.state.job_poll_seconds = 0.05  # the timed loop's pace: a pushed stream must ignore it
    app.state.job_stream_seconds = 2.5
    app.state.sse_safety_poll_seconds = 30.0
    app.state.sse_min_fetch_seconds = 0.0
    return app


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "viewer")}
    org = seed.org(kind="team", members={p["expert"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["viewer"]: "viewer"})
    p["job"] = seed.job(p["case"])
    return p


class Spy:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[tuple[float, bool, dict[str, object] | None]] = []
        real = case_events._jobs

        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, object] | None:
            result = await real(*args, **kwargs)
            self.calls.append((asyncio.get_running_loop().time(), bool(kwargs.get("check_access", True)), result))
            return result

        monkeypatch.setattr(case_events, "_jobs", wrapper)


def _events(resp: httpx.Response) -> list[dict[str, Any]]:
    return [json.loads(line[6:]) for line in resp.text.splitlines() if line.startswith("data: ")]


async def test_an_update_reaches_the_stream_within_a_second_with_no_other_fetch(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = Spy(monkeypatch)
    client = await client_for(team["expert"])
    stream = asyncio.create_task(client.get(f"/v1/cases/{team['case']}/events"))
    await asyncio.sleep(0.8)
    assert len(spy.calls) == 1, "one fetch at connect, none while idle (the timed loop would have made ~16)"
    admin.execute("update app.jobs set status = 'running', step = 'ocr' where id = %s", (team["job"],))
    sent = asyncio.get_running_loop().time()
    resp = await asyncio.wait_for(stream, 10)

    assert len(spy.calls) == 2, "one fetch for the notify, no more"
    when, _, result = spy.calls[1]
    assert when - sent < 1.0
    assert result is not None and result["jobs"][0]["status"] == "running"  # type: ignore[index]
    states = [e["jobs"][0]["status"] for e in _events(resp)]
    assert states == ["queued", "running"]


async def test_a_notify_for_another_case_does_not_make_this_stream_fetch(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    seed: Seed,
    admin: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    other_person = seed.person()
    other_org = seed.org(kind="team", members={other_person: "owner"})
    other_job = seed.job(seed.case(other_org, other_person))
    spy = Spy(monkeypatch)
    stream = asyncio.create_task((await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/events"))
    await asyncio.sleep(0.4)
    admin.execute("update app.jobs set status = 'running' where id = %s", (other_job,))
    await asyncio.wait_for(stream, 10)
    assert len(spy.calls) == 1


async def test_the_safety_tick_still_fetches_when_nothing_is_pushed(
    client_for: ClientFor, team: dict[str, uuid.UUID], app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    app.state.sse_safety_poll_seconds = 0.4
    app.state.job_stream_seconds = 1.0
    spy = Spy(monkeypatch)
    await asyncio.wait_for((await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/events"), 10)
    assert len(spy.calls) == 3, "connect, then a tick at 0.4 s and at 0.8 s"


async def test_keepalives_go_every_heartbeat_with_no_notify(
    client_for: ClientFor, team: dict[str, uuid.UUID], app: FastAPI
) -> None:
    app.state.job_heartbeat_seconds = 0.3
    app.state.job_stream_seconds = 1.0
    resp = await asyncio.wait_for((await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/events"), 10)
    assert resp.text.count(": keepalive") >= 2


async def test_access_is_checked_at_connect_and_only_every_recheck_interval_after(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    app: FastAPI,
    admin: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.sse_access_recheck_seconds = 1.0
    app.state.sse_safety_poll_seconds = 0.2
    app.state.job_stream_seconds = 1.5
    spy = Spy(monkeypatch)
    stream = asyncio.create_task((await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/events"))
    await asyncio.sleep(0.3)
    admin.execute("update app.jobs set pct = 10 where id = %s", (team["job"],))  # a pushed fetch: no access check
    await asyncio.wait_for(stream, 10)
    checks = [checked for _, checked, _ in spy.calls]
    # The check at connect is the route's `require_case_access`; the stream's own first look reuses it.
    assert checks[0] is False
    assert 1 <= checks.count(True) <= 3, "checked again once the interval passed, and not on every look"
    assert checks.count(False) >= 3, "a pushed or tick fetch between checks skips the check"


def test_the_default_recheck_interval_is_at_most_sixty_seconds(notifier: CaseNotifier) -> None:
    assert case_events.ACCESS_RECHECK_SECONDS <= 60.0
    recheck = AccessRecheck(SimpleNamespace(), notifier)  # a state with none of the settings: the defaults
    assert recheck.interval <= 60.0, "the gap between checks, with every setting at its default"


async def test_when_the_listener_is_down_the_stream_falls_back_to_the_timed_loop(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    app: FastAPI,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app.state.case_notifier = CaseNotifier("postgresql://nobody:x@127.0.0.1:1/none")  # never started: not ready
    app.state.job_poll_seconds = 0.05  # the case stream looks every 0.1 s while the listener is down
    app.state.job_stream_seconds = 1.2
    app.state.sse_safety_poll_seconds = 30.0
    spy = Spy(monkeypatch)
    stream = asyncio.create_task((await client_for(team["expert"])).get(f"/v1/cases/{team['case']}/events"))
    await asyncio.sleep(0.4)
    admin.execute("update app.jobs set status = 'running' where id = %s", (team["job"],))
    resp = await asyncio.wait_for(stream, 10)
    assert len(spy.calls) >= 6, "timed looks, not one look and a 30 s wait"
    assert [e["jobs"][0]["status"] for e in _events(resp)] == ["queued", "running"]


async def test_removing_a_member_ends_their_job_stream_and_the_reconnect_is_refused(
    client_for: ClientFor, team: dict[str, uuid.UUID], app: FastAPI, admin: psycopg.Connection
) -> None:
    app.state.sse_access_recheck_seconds = 0.5
    app.state.sse_safety_poll_seconds = 30.0  # the access check must not wait for the safety tick
    app.state.job_poll_seconds = 30.0
    app.state.job_stream_seconds = 5.0
    stream = asyncio.create_task((await client_for(team["viewer"])).get(f"/v1/jobs/{team['job']}/events"))
    await asyncio.sleep(0.3)
    admin.execute("delete from app.case_members where case_id = %s and person_id = %s", (team["case"], team["viewer"]))
    removed = asyncio.get_running_loop().time()
    resp = await asyncio.wait_for(stream, 5)
    assert resp.status_code == 200
    assert asyncio.get_running_loop().time() - removed < 1.2
    again = await (await client_for(team["viewer"])).get(f"/v1/jobs/{team['job']}/events")
    assert again.status_code == 404


async def test_removing_a_member_ends_the_stream_within_the_recheck_interval(
    client_for: ClientFor, team: dict[str, uuid.UUID], app: FastAPI, admin: psycopg.Connection
) -> None:
    app.state.sse_access_recheck_seconds = 0.5
    app.state.sse_safety_poll_seconds = 0.2
    app.state.job_stream_seconds = 5.0
    stream = asyncio.create_task((await client_for(team["viewer"])).get(f"/v1/cases/{team['case']}/events"))
    await asyncio.sleep(0.3)
    admin.execute("delete from app.case_members where case_id = %s and person_id = %s", (team["case"], team["viewer"]))
    removed = asyncio.get_running_loop().time()
    resp = await asyncio.wait_for(stream, 5)
    assert resp.status_code == 200
    assert asyncio.get_running_loop().time() - removed < 1.2
    again = await (await client_for(team["viewer"])).get(f"/v1/cases/{team['case']}/events")
    assert again.status_code == 404, "and the browser's reconnect is refused"


async def test_the_job_stream_is_woken_too(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    app: FastAPI,
) -> None:
    app.state.job_poll_seconds = 30.0  # only a push can make it look again inside the test
    app.state.job_stream_seconds = 3.0
    client = await client_for(team["expert"])
    stream = asyncio.create_task(client.get(f"/v1/jobs/{team['job']}/events"))
    await asyncio.sleep(0.4)
    admin.execute("update app.jobs set status = 'running' where id = %s", (team["job"],))
    await asyncio.sleep(0.3)
    admin.execute("update app.jobs set status = 'done' where id = %s", (team["job"],))
    resp = await asyncio.wait_for(stream, 2.0)  # ends on done, well before its 3 s limit
    assert [e["status"] for e in _events(resp)] == ["queued", "running", "done"]
