"""Tenant isolation on asynchronous paths and after access is taken away (AUDIT-11).

Each test asserts the SAFE outcome. A test the system fails is marked `xfail(strict=True, reason="AUDIT-11 ...")`
only after the same request was shown to succeed for the legitimate owner and the rows were inspected.

A. Jobs: another tenant's job, its progress stream, every enqueue and retry route, the support job routes, and a
   worker context that names one tenant's job and another tenant's case.
B. Event streams: an attacker gets 404; a member who is removed (or whose session is revoked) while a stream is open.
   The lag between the change and the end of the stream is measured and printed (`-s`).
C. The dictation WebSocket (L8): an attacker cannot connect; a member removed while connected.
D. Idempotency-Key: one person's key and answer are never replayed to another person, on another case, or after
   access is gone.
E. Downloads: every route that hands out a file, with the victim's ids, and with the attacker's own case plus the
   victim's object id. A test is skipped (reason "no positive control") when the legitimate owner gets 404 too:
   the local test cell has no stored files, so the 404 says nothing about the id.
F. The e-sign webhook (L6).
G. The colleague spot-check link (T9), after the case or the report has moved on.
H. The same signed-in session before and after each change of access: case member removed, organisation membership
   ended, case reassigned, organisation closed, account closed, session revoked, support grant expired or revoked,
   invite cancelled and then accepted.

The world is built by `Seed` and `fill_every_case_table` with its own people and organisations. Nothing here
resembles a record: markers are made-up strings and the output carries ids, table names and status codes only.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.ai.recordings import FixtureStore
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.email import ORG_INVITE
from app.core.jobs import JobContext
from app.core.notify import CaseNotifier
from app.core.speech import RecordedSpeech, TranscriptSegment
from app.features.report.spot_checks import code_hash, items_path, seal, token_hash
from tests.support.ai import cell
from tests.support.auth import cookie_header
from tests.support.case_rows import CaseWorld, fill_every_case_table
from tests.support.contracts import add_report_version
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.seed import FIXTURE_PACKS, Seed
from tests.support.ws import ws_connect

pytestmark = pytest.mark.anyio

ORIGIN = "https://app.test"
SECRET = "zz-esign-secret-for-isolation-tests"
AUDIO = [b"synthetic-audio-frame-1" * 40, b"synthetic-audio-frame-2" * 40]
SEGMENTS = [TranscriptSegment("synthetic words one", 0, 900), TranscriptSegment("synthetic words two", 900, 1800)]
LAGS: dict[str, float] = {}
_LAGS_EXPECTED = 4  # the timed and the pushed stream, each for the case and the job


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


# --- the world ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tenant:
    org: uuid.UUID
    owner: uuid.UUID
    admin_p: uuid.UUID
    expert: uuid.UUID
    assistant: uuid.UUID
    viewer: uuid.UUID
    case: uuid.UUID
    job: uuid.UUID
    page_no: int
    document: uuid.UUID
    version: uuid.UUID
    invoice: uuid.UUID
    package: uuid.UUID
    meeting: uuid.UUID


@dataclass(frozen=True)
class World:
    victim: Tenant
    attacker: Tenant
    outsider: uuid.UUID


def _first(admin: psycopg.Connection, query: str, case: uuid.UUID) -> Any:
    row = admin.execute(query, (case,)).fetchone()
    return row[0] if row else uuid.uuid4()  # a made-up id when the filled world has none of this kind


def _tenant(admin: psycopg.Connection, seed: Seed) -> Tenant:
    owner, admin_p, expert, assistant, viewer = (seed.person() for _ in range(5))
    org = seed.org(
        members={owner: "owner", admin_p: "admin", expert: "expert", assistant: "assistant", viewer: "viewer"}
    )
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    admin.execute(  # before the case serves a report: declarations are fixed after that
        "insert into app.declarations (expert_id, jurisdiction, kind) values (%s, 'zz-test', 'zz_kind')", (expert,)
    )
    fill_every_case_table(admin, seed, CaseWorld(case, expert, org))
    bundle = "(select bundle_id from app.cases where id = %s)"
    return Tenant(
        org=org,
        owner=owner,
        admin_p=admin_p,
        expert=expert,
        assistant=assistant,
        viewer=viewer,
        case=case,
        job=seed.job(case, requested_by=expert),
        page_no=_first(
            admin, f"select bundle_page_no from app.pages where bundle_id = {bundle} order by 1 limit 1", case
        ),
        document=_first(admin, f"select id from app.documents where bundle_id = {bundle} limit 1", case),
        version=_first(admin, "select id from app.report_versions where case_id = %s limit 1", case),
        invoice=_first(admin, "select id from app.invoices where case_id = %s limit 1", case),
        package=_first(admin, "select id from app.offline_packages where case_id = %s limit 1", case),
        meeting=_first(admin, "select id from app.expert_meetings where case_id = %s limit 1", case),
    )


@pytest.fixture(scope="module")
def world(admin: psycopg.Connection) -> World:
    seed = Seed(admin)
    victim = _tenant(admin, seed)
    attacker = _tenant(admin, seed)
    outsider = seed.person()
    seed.org(kind="personal", members={outsider: "owner"})
    return World(victim, attacker, outsider)


@dataclass(frozen=True)
class Team:
    """A small team for one test that changes access: owner, admin, named expert, assistant, viewer, one case."""

    org: uuid.UUID
    owner: uuid.UUID
    admin_p: uuid.UUID
    expert: uuid.UUID
    assistant: uuid.UUID
    viewer: uuid.UUID
    case: uuid.UUID
    job: uuid.UUID


@pytest.fixture
def team(seed: Seed) -> Team:
    owner, admin_p, expert, assistant, viewer = (seed.person() for _ in range(5))
    org = seed.org(
        members={owner: "owner", admin_p: "admin", expert: "expert", assistant: "assistant", viewer: "viewer"}
    )
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    return Team(org, owner, admin_p, expert, assistant, viewer, case, seed.job(case, requested_by=expert))


@pytest.fixture(scope="module")
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def recordings(tmp_path: Path) -> Path:
    return tmp_path / "speech"


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], crypto: CaseCrypto, recordings: Path) -> FastAPI:
    speech = RecordedSpeech(cell(), FixtureStore(recordings))
    app = build_app(api_db, auth_env, speech=speech, crypto=crypto)
    pack = app.state.packs.combine(
        FIXTURE_PACKS["jurisdiction"], FIXTURE_PACKS["case_type"], FIXTURE_PACKS["specialty"]
    )
    speech.record(b"".join(AUDIO), **pack.speech(), segments=SEGMENTS)
    return app


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
async def notifier(api_dsn: str) -> AsyncIterator[CaseNotifier]:
    n = CaseNotifier(api_dsn, backoff_min=0.05, backoff_max=0.2, ping_seconds=5)
    await n.start()
    await n.wait_ready(10)
    yield n
    await n.stop()


def _count(admin: psycopg.Connection, query: str, *params: Any) -> int:
    row = admin.execute(query, params).fetchone()
    assert row is not None
    return int(row[0])


def _job_rows(admin: psycopg.Connection, case: uuid.UUID) -> int:
    return _count(admin, "select count(*) from app.jobs where case_id = %s", case)


def _error(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["code"])
    except Exception:  # noqa: BLE001
        return "no error envelope"


# --- A. jobs -------------------------------------------------------------------------------------------------------


async def test_a_attacker_cannot_read_a_victims_job_or_its_progress_stream(world: World, client_for: ClientFor) -> None:
    attacker = await client_for(world.attacker.expert)
    for path in (f"/v1/jobs/{world.victim.job}", f"/v1/jobs/{world.victim.job}/events"):
        response = await asyncio.wait_for(attacker.get(path), 10)
        assert response.status_code == 404, f"GET {path}: {response.status_code}"
        assert str(world.victim.case) not in response.text
    owner = await client_for(world.victim.expert)
    mine = await owner.get(f"/v1/jobs/{world.victim.job}")
    assert mine.status_code == 200, "control: the owner reads the job"


ENQUEUE = [
    "/process",
    "/process/cancel",
    "/process/retry",
    "/reprocess",
    "/export",
    "/transcript/retry",
    "/transcript/download",
    "/report/build",
    "/trial-pack",
    "/offline-packages",
]


@pytest.mark.parametrize("suffix", ENQUEUE)
async def test_a_attacker_cannot_enqueue_or_retry_a_job_on_a_victims_case(
    suffix: str, world: World, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    attacker = await client_for(world.attacker.expert)
    before = (
        _job_rows(admin, world.victim.case),
        _count(admin, "select count(*) from app.jobs where requested_by = %s", world.attacker.expert),
    )
    response = await attacker.post(f"/v1/cases/{world.victim.case}{suffix}", json={})
    after = (
        _job_rows(admin, world.victim.case),
        _count(admin, "select count(*) from app.jobs where requested_by = %s", world.attacker.expert),
    )
    assert response.status_code == 404, f"POST {suffix}: {response.status_code} {_error(response)}"
    assert after == before, f"POST {suffix}: job rows changed {before} -> {after}"
    try:
        own = await attacker.post(f"/v1/cases/{world.attacker.case}{suffix}", json={})
    except Exception:  # noqa: BLE001 - the route got past access and failed later (no stored case key): a control
        return
    if own.status_code == 404:
        pytest.skip(f"no positive control: the attacker's own case is 404 on POST {suffix} too")


_SUPPORT_JOB_FLAGS = {"resume": "support_job_resume", "requeue": "support_job_requeue"}
_JOB_STATE_FOR = {"resume": "paused", "requeue": "failed"}


@pytest.mark.parametrize("action", ["resume", "requeue"])
async def test_a_only_support_staff_can_resume_or_requeue_a_job_through_the_support_routes(
    action: str, world: World, seed: Seed, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    """The routes are behind a feature flag, and `require_flag` answers 404 before the staff check: so the flag is
    switched ON for everyone tested here. A 404 for a non-staff person can then only come from the staff check, and
    real staff succeed on the same job (the control), which also proves the flag is on."""
    staff = seed.person()
    seed.org(kind="personal", members={staff: "owner"})
    seed.support_staff(staff)
    everyone = [
        staff,
        world.attacker.expert,
        world.attacker.owner,
        world.victim.admin_p,
        world.victim.assistant,
        world.outsider,
    ]
    for person in everyone:
        seed.flag(_SUPPORT_JOB_FLAGS[action], "person", person)
    job = seed.job(world.victim.case, requested_by=world.victim.expert)
    for step in ("running", _JOB_STATE_FOR[action]):  # the database allows only the queue's own order of states
        admin.execute("update app.jobs set status = %s where id = %s", (step, job))
    row = "select to_jsonb(j) from app.jobs j where id = %s"
    actions = "select count(*) from app.job_support_actions where job_id = %s"
    before = admin.execute(row, (job,)).fetchone()
    assert admin.execute(actions, (job,)).fetchone() == (0,)
    for person in everyone[1:]:
        response = await (await client_for(person)).post(
            f"/v1/support/jobs/{job}/{action}", json={"reason_code": "fault_fixed"}
        )
        assert response.status_code == 404, f"{action}: {response.status_code} {_error(response)}"
        assert str(world.victim.case) not in response.text
        assert admin.execute(row, (job,)).fetchone() == before, f"{action}: the job changed"
        assert admin.execute(actions, (job,)).fetchone() == (0,), f"{action}: a support action was recorded"
    done = await (await client_for(staff)).post(f"/v1/support/jobs/{job}/{action}", json={"reason_code": "fault_fixed"})
    assert done.status_code == 200, f"control: real staff get {done.status_code} {_error(done)}"
    assert admin.execute(actions, (job,)).fetchone() == (1,), "control: staff's action was not recorded"


async def test_a_a_victims_job_id_under_the_attackers_own_case_is_not_found(
    world: World, client_for: ClientFor
) -> None:
    attacker = await client_for(world.attacker.expert)
    for path in (
        f"/v1/cases/{world.attacker.case}/export/{world.victim.job}/download",
        f"/v1/cases/{world.attacker.case}/transcript/download/{world.victim.job}",
    ):
        response = await attacker.get(path)
        assert response.status_code == 404, f"GET {path}: {response.status_code}"


async def test_a_a_worker_context_with_one_tenants_job_and_anothers_case_cannot_move_its_progress(
    world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    job = world.victim.job
    row = "select to_jsonb(j) from app.jobs j where id = %s"
    before_job = admin.execute(row, (job,)).fetchone()
    await JobContext(worker_db, job, world.attacker.case, "process_bundle", 1).progress(77, step="zz_step")
    assert admin.execute(row, (job,)).fetchone() == before_job
    # control: the same call with the job's own case does change the job
    await JobContext(worker_db, job, world.victim.case, "process_bundle", 1).progress(33, step="zz_step")
    assert admin.execute("select pct from app.jobs where id = %s", (job,)).fetchone() == (33,)


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 NEW-1: a worker context naming one case's job and another case's id writes a job_steps row"
    " (job_id = the first case's job, case_id = the second case): app.job_steps has no (job_id, case_id)"
    " consistency, only the case_id RLS check (supabase/migrations/0007_foundation_files_jobs.sql:664-669, 711-712)",
)
async def test_a_a_worker_context_with_one_tenants_job_and_anothers_case_cannot_record_a_step(
    world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    job = world.victim.job
    steps_before = _count(admin, "select count(*) from app.job_steps where job_id = %s", job)

    async def nothing(_: Any) -> None:
        return None

    try:
        await JobContext(worker_db, job, world.attacker.case, "process_bundle", 1).step("zz_cross", nothing)
    except psycopg.Error:
        pass  # a refusal is the safe outcome
    rows = _count(admin, "select count(*) from app.job_steps where job_id = %s", job)
    admin.execute("delete from app.job_steps where job_id = %s and step = 'zz_cross'", (job,))
    assert rows == steps_before, "a step row for another case's job was written under this case"


# --- B. event streams ----------------------------------------------------------------------------------------------


async def test_b_attacker_gets_404_on_a_victims_case_event_stream(world: World, client_for: ClientFor) -> None:
    attacker = await client_for(world.attacker.expert)
    response = await asyncio.wait_for(attacker.get(f"/v1/cases/{world.victim.case}/events"), 10)
    assert response.status_code == 404
    assert str(world.victim.job) not in response.text
    owner = await client_for(world.victim.expert)
    assert (await owner.get(f"/v1/cases/{world.victim.case}")).status_code == 200, "control"


async def _remove_member(client: httpx.AsyncClient, admin: psycopg.Connection, team: Team, person: uuid.UUID) -> None:
    member = admin.execute(
        "select id from app.case_members where case_id = %s and person_id = %s and removed_at is null",
        (team.case, person),
    ).fetchone()
    assert member is not None
    response = await client.delete(f"/v1/cases/{team.case}/members/{member[0]}")
    assert response.status_code == 204, response.text


async def _time_to_end(stream: asyncio.Task[httpx.Response], limit: float) -> float:
    started = time.monotonic()
    await asyncio.wait_for(stream, limit)
    return time.monotonic() - started


@pytest.mark.parametrize("target", ["case", "job"])
async def test_b_a_member_removed_while_a_timed_stream_is_open_loses_it_quickly(
    target: str, team: Team, app: FastAPI, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    app.state.job_poll_seconds = 0.05  # the smallest the existing stream tests use
    app.state.job_stream_seconds = 6.0
    path = f"/v1/cases/{team.case}/events" if target == "case" else f"/v1/jobs/{team.job}/events"
    member = await client_for(team.viewer)
    stream = asyncio.create_task(member.get(path))
    await asyncio.sleep(0.4)
    assert not stream.done(), "control: the stream is open"
    await _remove_member(await client_for(team.expert), admin, team, team.viewer)
    lag = await _time_to_end(stream, 5.5)
    LAGS[f"timed {target} stream, member removed (poll 0.05 s)"] = lag
    print(f"AUDIT-11 LAG timed {target} stream after member removal: {lag:.2f} s (stream limit 6.0 s)")
    assert lag < 3.0, f"the stream stayed open {lag:.1f} s after access was removed"
    after = await member.get(path)
    assert after.status_code == 404


@pytest.mark.parametrize("target", ["case", "job"])
async def test_b_a_member_removed_while_a_pushed_stream_is_open_loses_it_within_the_recheck_interval(
    target: str,
    team: Team,
    app: FastAPI,
    notifier: CaseNotifier,
    client_for: ClientFor,
    admin: psycopg.Connection,
) -> None:
    app.state.case_notifier = notifier
    app.state.sse_access_recheck_seconds = 1.0  # the interval is half of this: 0.5 s (the default is 30 s)
    app.state.sse_safety_poll_seconds = 30.0
    app.state.job_poll_seconds = 30.0
    app.state.job_stream_seconds = 8.0
    path = f"/v1/cases/{team.case}/events" if target == "case" else f"/v1/jobs/{team.job}/events"
    member = await client_for(team.viewer)
    stream = asyncio.create_task(member.get(path))
    await asyncio.sleep(0.4)
    assert not stream.done()
    await _remove_member(await client_for(team.expert), admin, team, team.viewer)
    lag = await _time_to_end(stream, 7.5)
    LAGS[f"pushed {target} stream, member removed (interval 0.5 s)"] = lag
    print(f"AUDIT-11 LAG pushed {target} stream after member removal: {lag:.2f} s (interval 0.5 s, limit 8.0 s)")
    assert lag < 2.0, f"the stream stayed open {lag:.1f} s after access was removed"
    assert (await member.get(path)).status_code == 404


async def test_b_a_stream_ends_when_the_whole_organisation_membership_ends(
    team: Team, app: FastAPI, client_for: ClientFor, admin: psycopg.Connection, seed: Seed
) -> None:
    app.state.job_poll_seconds = 0.05
    app.state.job_stream_seconds = 6.0
    admin_client = await client_for(team.admin_p)
    assert (await admin_client.get(f"/v1/cases/{team.case}")).status_code == 200, "control: an admin sees the case"
    stream = asyncio.create_task(admin_client.get(f"/v1/cases/{team.case}/events"))
    await asyncio.sleep(0.4)
    seed.end_membership(team.org, team.admin_p)
    lag = await _time_to_end(stream, 5.5)
    print(f"AUDIT-11 LAG timed case stream after organisation membership ended: {lag:.2f} s")
    assert lag < 3.0


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 NEW-2: an open event stream ignores a revoked session until job_stream_seconds; the re-check"
    " asks about case access only (app/core/sse.py:46-66, app/core/routes/case_events.py:70)",
)
async def test_b_a_stream_ends_when_the_session_behind_it_is_revoked(
    team: Team, app: FastAPI, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    app.state.job_poll_seconds = 0.05
    app.state.job_stream_seconds = 4.0
    member = await client_for(team.expert)
    stream = asyncio.create_task(member.get(f"/v1/cases/{team.case}/events"))
    await asyncio.sleep(0.4)
    admin.execute(
        "update app.sessions set revoked_at = now(), revoked_reason = 'signed_out_everywhere' where person_id = %s",
        (team.expert,),
    )
    after = await member.get(f"/v1/cases/{team.case}")
    assert after.status_code == 401, "control: a new request on the revoked session is refused"
    started = time.monotonic()
    await asyncio.wait_for(stream, 5.5)
    lag = time.monotonic() - started
    print(f"AUDIT-11 LAG timed case stream after the session was revoked: {lag:.2f} s (stream limit 4.0 s)")
    assert lag < 2.0, f"the revoked session's stream stayed open {lag:.1f} s"


# --- C. the dictation WebSocket ------------------------------------------------------------------------------------


def _socket_headers(seed: Seed, person: uuid.UUID) -> list[tuple[str, str]]:
    return [("cookie", cookie_header(seed.session(person))), ("origin", ORIGIN)]


def _dictation(case: uuid.UUID) -> str:
    return f"/v1/cases/{case}/dictation?context=opinion"


async def test_c_attacker_cannot_open_the_dictation_socket_on_a_victims_case(
    world: World, app: FastAPI, seed: Seed
) -> None:
    async with ws_connect(app, _dictation(world.victim.case), _socket_headers(seed, world.attacker.expert)) as ws:
        assert not ws.accepted
        assert ws.close_code == 4404
    async with ws_connect(app, _dictation(world.victim.case), _socket_headers(seed, world.victim.expert)) as ws:
        assert ws.accepted, "control: the named expert connects"


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 L8: dictation checks case access only at connect; a removed member keeps getting segments"
    " for the audio they send (app/core/routes/dictation.py:70-75)",
)
async def test_c_a_member_removed_after_connecting_can_no_longer_send_audio(
    team: Team, app: FastAPI, seed: Seed, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    async with ws_connect(app, _dictation(team.case), _socket_headers(seed, team.assistant)) as control:
        assert control.accepted, "control: a member who may write comments connects"
    async with ws_connect(app, _dictation(team.case), _socket_headers(seed, team.assistant)) as ws:
        assert ws.accepted
        await _remove_member(await client_for(team.expert), admin, team, team.assistant)
        for frame in AUDIO:
            await ws.send_bytes(frame)
        await ws.send_json({"type": "end"})
        messages = await ws.receive_until_closed()
    segments = [m for m in messages if m.get("type") == "segment"]
    print(f"AUDIT-11 SOCKET after member removal: {len(segments)} segments returned, close code {ws.close_code}")
    assert segments == [], "the removed member's audio was still transcribed and returned"


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 L8: dictation checks the session only at connect; a revoked session keeps getting segments"
    " for the audio it sends (app/core/routes/dictation.py:70-75)",
)
async def test_c_a_session_revoked_after_connecting_can_no_longer_send_audio(
    team: Team, app: FastAPI, seed: Seed, admin: psycopg.Connection
) -> None:
    async with ws_connect(app, _dictation(team.case), _socket_headers(seed, team.expert)) as ws:
        assert ws.accepted
        admin.execute(
            "update app.sessions set revoked_at = now(), revoked_reason = 'signed_out_everywhere' where person_id = %s",
            (team.expert,),
        )
        for frame in AUDIO:
            await ws.send_bytes(frame)
        await ws.send_json({"type": "end"})
        messages = await ws.receive_until_closed()
    segments = [m for m in messages if m.get("type") == "segment"]
    print(f"AUDIT-11 SOCKET after session revoked: {len(segments)} segments returned, close code {ws.close_code}")
    assert segments == [], "the revoked session's audio was still transcribed and returned"


# --- D. Idempotency-Key --------------------------------------------------------------------------------------------


def _transcript(key: str) -> dict[str, Any]:
    return {
        "json": {"context": "opinion", "text": "zz words", "duration_seconds": 3},
        "headers": {"idempotency-key": key},
    }


async def test_d_a_key_used_by_one_person_is_never_replayed_to_another(world: World, client_for: ClientFor) -> None:
    key = secrets.token_hex(16)
    owner = await client_for(world.victim.expert)
    first = await owner.post(f"/v1/cases/{world.victim.case}/transcripts", **_transcript(key))
    assert first.status_code == 201, first.text
    again = await owner.post(f"/v1/cases/{world.victim.case}/transcripts", **_transcript(key))
    assert again.headers.get("idempotent-replayed") == "true" and again.json() == first.json(), "control"

    attacker = await client_for(world.attacker.expert)
    on_victim = await attacker.post(f"/v1/cases/{world.victim.case}/transcripts", **_transcript(key))
    assert on_victim.status_code == 404
    assert "idempotent-replayed" not in on_victim.headers and first.json()["id"] not in on_victim.text
    on_own = await attacker.post(f"/v1/cases/{world.attacker.case}/transcripts", **_transcript(key))
    assert on_own.status_code == 201, on_own.text
    assert "idempotent-replayed" not in on_own.headers
    assert on_own.json()["id"] != first.json()["id"]


async def test_d_the_same_key_on_another_case_is_refused_not_replayed(world: World, client_for: ClientFor) -> None:
    key = secrets.token_hex(16)
    owner = await client_for(world.victim.expert)
    first = await owner.post(f"/v1/cases/{world.victim.case}/transcripts", **_transcript(key))
    assert first.status_code == 201
    other = await owner.post(f"/v1/cases/{world.victim.case}/transcripts?x=1", **_transcript(key))
    assert other.status_code == 422 and _error(other) == "idempotency_key_reused"
    assert first.json()["id"] not in other.text


async def test_d_a_removed_member_retrying_a_key_gets_404_not_the_saved_answer(
    team: Team, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    key = secrets.token_hex(16)
    member = await client_for(team.assistant)
    first = await member.post(f"/v1/cases/{team.case}/transcripts", **_transcript(key))
    assert first.status_code == 201, first.text
    await _remove_member(await client_for(team.expert), admin, team, team.assistant)
    retry = await member.post(f"/v1/cases/{team.case}/transcripts", **_transcript(key))
    assert retry.status_code == 404, f"{retry.status_code} {_error(retry)}"
    assert "idempotent-replayed" not in retry.headers and first.json()["id"] not in retry.text


# --- E. downloads --------------------------------------------------------------------------------------------------

DOWNLOADS: list[tuple[str, str, dict[str, str]]] = [
    ("page image", "/v1/cases/{case}/pages/{page}/image", {}),
    ("thumbnail", "/v1/cases/{case}/pages/{page}/thumbnail", {}),
    ("page as PDF (report preview)", "/v1/cases/{case}/report/preview/{page}", {}),
    ("original document", "/v1/cases/{case}/documents/{doc}/file", {}),
    ("report PDF", "/v1/cases/{case}/report/versions/{version}/download", {"format": "pdf"}),
    ("report DOCX", "/v1/cases/{case}/report/versions/{version}/download", {"format": "docx"}),
    ("invoice PDF", "/v1/cases/{case}/invoices/{invoice}/pdf", {}),
    ("export ZIP", "/v1/cases/{case}/export/{job}/download", {}),
    ("export file list", "/v1/cases/{case}/export/files", {}),
    ("trial pack", "/v1/cases/{case}/trial-pack/latest/download", {}),
    ("trial pack preview", "/v1/cases/{case}/trial-pack/preview", {}),
    ("concordance export", "/v1/cases/{case}/concordance/export", {}),
    ("certificate of deletion", "/v1/cases/{case}/deletion-certificate", {}),
    ("offline witness-box copy", "/v1/cases/{case}/offline-packages/{pkg}/download", {}),
    ("transcript download", "/v1/cases/{case}/transcript/download/{job}", {}),
    ("case files", "/v1/cases/{case}/files", {}),
]
_OBJECTS = ("{page}", "{doc}", "{version}", "{invoice}", "{job}", "{pkg}")


def _fill(template: str, case: uuid.UUID, objects: Tenant) -> str:
    return (
        template.replace("{case}", str(case))
        .replace("{page}", str(objects.page_no))
        .replace("{doc}", str(objects.document))
        .replace("{version}", str(objects.version))
        .replace("{invoice}", str(objects.invoice))
        .replace("{job}", str(objects.job))
        .replace("{pkg}", str(objects.package))
    )


def _nothing_came_back(response: httpx.Response) -> bool:
    kind = response.headers.get("content-type", "")
    return (
        not kind.startswith(("application/pdf", "application/zip", "application/octet-stream", "image/"))
        and len(response.content) < 4000
    )


@pytest.mark.parametrize(("name", "template", "query"), DOWNLOADS, ids=[d[0] for d in DOWNLOADS])
async def test_e_attacker_gets_404_for_a_victims_download(
    name: str, template: str, query: dict[str, str], world: World, client_for: ClientFor
) -> None:
    attacker = await client_for(world.attacker.expert)
    url = _fill(template, world.victim.case, world.victim)
    response = await attacker.get(url, params=query)
    assert response.status_code == 404, f"{name}: {response.status_code} {_error(response)}"
    assert _nothing_came_back(response)
    owner = await client_for(world.victim.expert)
    try:
        mine = await owner.get(url, params=query)
    except Exception:  # noqa: BLE001 - the owner got past access and the handler failed on the filled rows: a control
        return
    if mine.status_code == 404:
        pytest.skip(f"no positive control: the owner gets 404 for {name} too (no stored file in the test cell)")


@pytest.mark.parametrize(
    ("name", "template", "query"),
    [d for d in DOWNLOADS if any(o in d[1] for o in _OBJECTS)],
    ids=[d[0] for d in DOWNLOADS if any(o in d[1] for o in _OBJECTS)],
)
async def test_e_the_attackers_case_with_a_victims_object_id_is_not_served_and_is_no_oracle(
    name: str, template: str, query: dict[str, str], world: World, client_for: ClientFor
) -> None:
    """The test cell has no file store, so some routes answer 503 before they look at the id. The safe outcome is
    then: never 200, and the same answer as for an id that exists nowhere (the id is not probed)."""
    attacker = await client_for(world.attacker.expert)
    nowhere = replace(
        world.victim,
        job=uuid.uuid4(),
        page_no=999_999,
        document=uuid.uuid4(),
        version=uuid.uuid4(),
        invoice=uuid.uuid4(),
        package=uuid.uuid4(),
    )
    response = await attacker.get(_fill(template, world.attacker.case, world.victim), params=query)
    baseline = await attacker.get(_fill(template, world.attacker.case, nowhere), params=query)
    assert response.status_code != 200 and _nothing_came_back(response), f"{name}: {response.status_code}"
    assert response.status_code == baseline.status_code, (
        f"{name}: victim id {response.status_code}, made-up id {baseline.status_code}"
    )
    own = await attacker.get(_fill(template, world.attacker.case, world.attacker), params=query)
    if own.status_code == baseline.status_code:
        pytest.skip(f"no positive control: the attacker's own object answers like a made-up id for {name}")


async def test_e_the_signature_image_is_the_signed_in_persons_own(world: World, client_for: ClientFor) -> None:
    victim = await (await client_for(world.victim.expert)).get("/v1/me/signature/image")
    attacker = await (await client_for(world.attacker.expert)).get("/v1/me/signature/image")
    assert attacker.status_code in (200, 404)
    if victim.status_code != 200:
        pytest.skip("no positive control: the victim's own signature image is not served in the test cell")
    assert attacker.content != victim.content


# --- F. the e-sign webhook -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Meeting:
    id: uuid.UUID
    case: uuid.UUID
    reference: str
    prepared: str


def _meeting(admin: psycopg.Connection, case: uuid.UUID) -> Meeting:
    reference = "zz-ref-" + secrets.token_hex(16)
    prepared = hashlib.sha256(secrets.token_bytes(16)).hexdigest()
    row = admin.execute(
        "insert into app.expert_meetings (case_id, type, discussion_on, sign_by, sign_rule_ref, send_by,"
        " send_rule_ref, status, prepared_sha256, prepared_at, signing_reference_sha256)"
        " values (%s, 'zz_conference', '2026-11-02', '2026-11-09', 'Rule Z.9', '2026-11-23', 'Rule Z.9',"
        " 'out_for_signature', %s, now(), %s) returning id",
        (case, prepared, hashlib.sha256(reference.encode()).hexdigest()),
    ).fetchone()
    assert row is not None
    return Meeting(row[0], case, reference, prepared)


def _state(admin: psycopg.Connection, meeting: Meeting) -> tuple[Any, ...]:
    row = admin.execute(
        "select status, signed_sha256, certificate_sha256, signing_reference_sha256"
        " from app.expert_meetings where id = %s",
        (meeting.id,),
    ).fetchone()
    assert row is not None
    return row


def _signed_body(reference: str, document: str, **extra: Any) -> bytes:
    return json.dumps(
        {
            "reference": reference,
            "event": "signed",
            "signed_document_sha256": document,
            "certificate": base64.b64encode(b"zz certificate").decode(),
            **extra,
        }
    ).encode()


def _signature(body: bytes, secret: str = SECRET) -> dict[str, str]:
    return {"x-avodeck-signature": "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()}


@pytest.fixture
def esign(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ESIGN_WEBHOOK_SECRET", SECRET)


async def test_f_unsigned_and_wrongly_signed_callbacks_are_refused_and_change_nothing(
    world: World, esign: None, client_for: ClientFor, admin: psycopg.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    meeting = _meeting(admin, world.victim.case)
    before = _state(admin, meeting)
    body = _signed_body(meeting.reference, hashlib.sha256(b"zz signed").hexdigest())
    anyone = await client_for(None)
    for headers in (
        {},
        {"x-avodeck-signature": "sha256=" + "0" * 64},
        _signature(body, "zz-another-secret"),
        {"x-avodeck-signature": "garbage"},
    ):
        response = await anyone.post("/v1/webhooks/esign", content=body, headers=headers)
        assert response.status_code == 401 and _error(response) == "bad_signature", headers
    assert _state(admin, meeting) == before
    tampered = body.replace(b'"event": "signed"', b'"event": "declined"')
    assert tampered != body
    response = await anyone.post("/v1/webhooks/esign", content=tampered, headers=_signature(body))
    assert response.status_code == 401
    assert _state(admin, meeting) == before
    monkeypatch.delenv("ESIGN_WEBHOOK_SECRET")
    response = await anyone.post("/v1/webhooks/esign", content=body, headers=_signature(body))
    assert response.status_code == 401, "a cell with no secret accepts nothing"
    assert _state(admin, meeting) == before


async def test_f_a_callback_for_one_reference_changes_only_that_statement(
    world: World, esign: None, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    mine = _meeting(admin, world.attacker.case)
    theirs = _meeting(admin, world.victim.case)
    before_theirs = _state(admin, theirs)
    # The body names no case or package; extra fields that name the victim's are ignored, not obeyed.
    body = _signed_body(
        mine.reference,
        hashlib.sha256(b"zz signed").hexdigest(),
        case_id=str(world.victim.case),
        meeting_id=str(theirs.id),
        package_id=str(theirs.id),
    )
    anyone = await client_for(None)
    response = await anyone.post("/v1/webhooks/esign", content=body, headers=_signature(body))
    assert response.status_code == 200, response.text
    assert _state(admin, mine)[0] == "signed", "control: the named statement is signed"
    assert _state(admin, theirs) == before_theirs
    declined = json.dumps({"reference": theirs.reference, "event": "declined"}).encode()
    other = _meeting(admin, world.attacker.case)
    before_other = _state(admin, other)
    response = await anyone.post("/v1/webhooks/esign", content=declined, headers=_signature(declined))
    assert response.status_code == 200
    assert _state(admin, other) == before_other
    assert _state(admin, theirs)[0] == "draft"


async def test_f_a_second_callback_cannot_replace_a_signed_statements_hash(
    world: World, esign: None, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    meeting = _meeting(admin, world.victim.case)
    anyone = await client_for(None)
    first_hash = hashlib.sha256(b"zz first").hexdigest()
    body = _signed_body(meeting.reference, first_hash)
    assert (await anyone.post("/v1/webhooks/esign", content=body, headers=_signature(body))).status_code == 200
    forged = _signed_body(meeting.reference, hashlib.sha256(b"zz forged").hexdigest())
    response = await anyone.post("/v1/webhooks/esign", content=forged, headers=_signature(forged))
    assert response.status_code == 409
    assert _state(admin, meeting)[1] == first_hash


async def test_f_an_unknown_reference_is_404_and_changes_nothing(
    world: World, esign: None, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    meeting = _meeting(admin, world.victim.case)
    before = _state(admin, meeting)
    body = _signed_body("zz-unknown-" + secrets.token_hex(16), hashlib.sha256(b"zz").hexdigest())
    response = await (await client_for(None)).post("/v1/webhooks/esign", content=body, headers=_signature(body))
    assert response.status_code == 404
    assert _state(admin, meeting) == before


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 POLICY L6: allowed by docs/spec/permissions.md:68 (public routes take no session; the signed"
    " callback's signature is its only gate; no line of permissions.md mentions the document hash); owner decision."
    " A correctly signed callback with a document hash that differs from prepared_sha256 is"
    " recorded as signed (200); app.trial_esign_signed never compares them"
    " (supabase/migrations/0900_trial_tables.sql:393)."
    " A real signed PDF does differ from the prepared one, so this is by design unless a binding is added",
)
async def test_f_a_signed_callback_with_a_different_document_hash_than_was_prepared_is_refused(
    world: World, esign: None, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    meeting = _meeting(admin, world.victim.case)
    before = _state(admin, meeting)
    other_document = hashlib.sha256(b"zz some other document").hexdigest()
    assert other_document != meeting.prepared
    body = _signed_body(meeting.reference, other_document)
    response = await (await client_for(None)).post("/v1/webhooks/esign", content=body, headers=_signature(body))
    assert response.status_code in (409, 422), f"{response.status_code}: a different document was recorded as signed"
    assert _state(admin, meeting) == before


# --- G. the colleague spot-check link ------------------------------------------------------------------------------


class MemoryStorage:
    """Just enough of the case file store for the colleague's link: sealed items by path."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    async def put(self, path: str, data: bytes) -> None:
        self.objects[path] = data

    async def get(self, path: str) -> bytes:
        return self.objects[path]


@dataclass(frozen=True)
class Check:
    id: uuid.UUID
    case: uuid.UUID
    token: str
    code: str
    marker: str


@pytest.fixture
def spot_app(app: FastAPI, admin: psycopg.Connection) -> Iterator[tuple[FastAPI, MemoryStorage]]:
    store = MemoryStorage()
    app.state.storage = store
    if admin.execute("select to_regclass('app.spot_check_address_tries')").fetchone() != (None,):
        admin.execute("delete from app.spot_check_address_tries")
    yield app, store


def _check(
    admin: psycopg.Connection,
    store: MemoryStorage,
    case: uuid.UUID,
    version: uuid.UUID,
    creator: uuid.UUID,
    *,
    expires: str = "7 days",
) -> Check:
    check_id, token, code = uuid.uuid4(), secrets.token_urlsafe(24), f"{secrets.randbelow(10**6):06d}"
    hashed, marker = token_hash(token), f"zz-marker-{check_id.hex[:10]}"
    fact = uuid.uuid4()
    admin.execute(
        "insert into app.spot_checks (id, case_id, report_version_id, reviewer_email, fact_ids, token_hash, code_hash,"
        " code_sent_at, expires_at, created_by) values (%s, %s, %s, 'zz-reviewer@example.test', %s, %s, %s, now(),"
        " now() + %s::interval, %s)",
        (check_id, case, version, [fact], hashed, code_hash(hashed, code), expires, creator),
    )
    items = {"items": [{"fact_id": str(fact), "value": marker, "crop": None}]}
    store.objects[items_path(case, check_id)] = seal(token, check_id, json.dumps(items).encode())
    return Check(check_id, case, token, code, marker)


async def _open(app: FastAPI, check: Check, code: str | None = None) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        return await client.get(f"/v1/spot-checks/{check.token}", headers={"x-spot-check-code": code or check.code})


async def test_g_a_live_link_opens_with_its_code_and_only_with_its_own_code(
    world: World, spot_app: tuple[FastAPI, MemoryStorage], admin: psycopg.Connection
) -> None:
    app, store = spot_app
    mine = _check(admin, store, world.attacker.case, world.attacker.version, world.attacker.expert)
    theirs = _check(admin, store, world.victim.case, world.victim.version, world.victim.expert)
    response = await _open(app, theirs)
    assert response.status_code == 200 and response.json()["status"] == "ok", "control"
    assert theirs.marker in response.text and mine.marker not in response.text
    swapped = await _open(app, theirs, code=mine.code if mine.code != theirs.code else "000000")
    assert swapped.json()["status"] != "ok" and theirs.marker not in swapped.text
    crossed = Check(theirs.id, theirs.case, mine.token, theirs.code, theirs.marker)  # my token with their code
    answer = await _open(app, crossed)
    assert answer.json()["status"] != "ok" and theirs.marker not in answer.text


async def test_g_an_expired_link_returns_no_content(
    world: World, spot_app: tuple[FastAPI, MemoryStorage], admin: psycopg.Connection
) -> None:
    app, store = spot_app
    check = _check(admin, store, world.victim.case, world.victim.version, world.victim.expert)
    admin.execute("update app.spot_checks set expires_at = now() - interval '1 minute' where id = %s", (check.id,))
    response = await _open(app, check)
    assert response.json()["status"] == "expired" and check.marker not in response.text


_STILL_OPEN = pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 POLICY NEW-3: the link still opens after the case is settled or archived; only a withdrawn or"
    " deleted case closes it (supabase/migrations/0609_report_spot_check_closed_case.sql:25); owner decision",
)


@pytest.mark.parametrize(
    "status",
    [pytest.param("settled", marks=_STILL_OPEN), pytest.param("archived", marks=_STILL_OPEN), "withdrawn", "deleted"],
)
async def test_g_a_link_returns_no_content_once_its_case_has_closed_or_been_deleted(
    status: str, spot_app: tuple[FastAPI, MemoryStorage], admin: psycopg.Connection, seed: Seed
) -> None:
    app, store = spot_app
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    version = add_report_version(admin, case, status="served")
    check = _check(admin, store, case, version, expert)
    live = await _open(app, check)
    assert live.json()["status"] == "ok", "control: the link opens while the case is live"
    seed.case_status(case, "served")
    path = {"settled": ["settled"], "archived": ["settled", "archived"], "withdrawn": [], "deleted": []}[status]
    if status == "withdrawn":
        admin.execute("update app.cases set status = 'withdrawn' where id = %s", (case,))
    elif status == "deleted":
        admin.execute("update app.cases set status = 'deleted' where id = %s", (case,))
    for step in path:
        seed.case_status(case, step)
    response = await _open(app, check)
    assert response.json()["status"] != "ok", f"the link opened with the case {status}"
    assert check.marker not in response.text


async def test_g_a_link_returns_no_content_once_its_report_version_is_withdrawn(
    spot_app: tuple[FastAPI, MemoryStorage], admin: psycopg.Connection, seed: Seed
) -> None:
    app, store = spot_app
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    version = add_report_version(admin, case, status="served")
    check = _check(admin, store, case, version, expert)
    assert (await _open(app, check)).json()["status"] == "ok", "control"
    admin.execute(
        "update app.report_versions set status = 'withdrawn', withdrawn_reason = 'zz_reason' where id = %s", (version,)
    )
    response = await _open(app, check)
    assert response.json()["status"] != "ok", "the link opened with its report version withdrawn"
    assert check.marker not in response.text


async def test_g_a_link_returns_no_content_once_its_case_rows_are_deleted(
    spot_app: tuple[FastAPI, MemoryStorage], admin: psycopg.Connection, seed: Seed
) -> None:
    app, store = spot_app
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    case = seed.case(org, expert, status="active")
    version = add_report_version(admin, case, status="draft")  # a served version cannot be deleted
    check = _check(admin, store, case, version, expert)
    assert (await _open(app, check)).json()["status"] == "ok", "control"
    admin.execute("delete from app.cases where id = %s", (case,))
    response = await _open(app, check)
    assert response.json()["status"] == "unknown" and check.marker not in response.text


async def test_g_a_link_has_no_revoke_route(world: World, client_for: ClientFor) -> None:
    """There is no way for the expert to withdraw a link early: only `POST` and `GET /cases/{id}/spot-checks`."""
    expert = await client_for(world.victim.expert)
    for method in ("DELETE", "PATCH", "PUT"):
        response = await expert.request(method, f"/v1/cases/{world.victim.case}/spot-checks/{uuid.uuid4()}")
        assert response.status_code in (404, 405)
    response = await expert.post(f"/v1/cases/{world.victim.case}/spot-checks/{uuid.uuid4()}/revoke", json={})
    assert response.status_code in (404, 405), "a revoke route now exists: test the link after it"


# --- H. the same session before and after each change of access ----------------------------------------------------


async def _status(client: httpx.AsyncClient, case: uuid.UUID) -> tuple[int, int]:
    metadata = (await client.get(f"/v1/cases/{case}")).status_code
    content = (await client.get(f"/v1/cases/{case}/pages")).status_code
    return metadata, content


async def test_h_a_case_member_removed_loses_the_case_on_the_next_request(
    team: Team, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    viewer = await client_for(team.viewer)
    assert await _status(viewer, team.case) == (200, 200), "control"
    await _remove_member(await client_for(team.expert), admin, team, team.viewer)
    assert await _status(viewer, team.case) == (404, 404)
    assert (await viewer.get(f"/v1/jobs/{team.job}")).status_code == 404
    assert (await viewer.get(f"/v1/cases/{team.case}/files")).status_code == 404


async def test_h_an_organisation_member_whose_membership_ends_loses_the_case(
    team: Team, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    owner = await client_for(team.owner)
    org_admin = await client_for(team.admin_p)
    assistant = await client_for(team.assistant)
    assert (await org_admin.get(f"/v1/cases/{team.case}")).status_code == 200, "control: an admin sees the case"
    assert await _status(assistant, team.case) == (200, 200), "control: a case member sees the case"
    for person in (team.admin_p, team.assistant):
        membership = admin.execute(
            "select id from app.memberships where organisation_id = %s and person_id = %s", (team.org, person)
        ).fetchone()
        assert membership is not None
        response = await owner.delete(f"/v1/organisations/{team.org}/members/{membership[0]}")
        assert response.status_code in (200, 204), f"{response.status_code} {_error(response)}"
    assert (await org_admin.get(f"/v1/cases/{team.case}")).status_code == 404
    left = await _status(assistant, team.case)
    rows = _count(
        admin,
        "select count(*) from app.case_members where case_id = %s and person_id = %s and removed_at is null",
        team.case,
        team.assistant,
    )
    print(f"AUDIT-11 membership ended: assistant sees {left}, live case_members rows left: {rows}")
    assert left == (404, 404), (
        f"a person who left the organisation still reaches its case ({left}, {rows} case_members rows)"
    )


async def test_h_leaving_the_organisation_ends_access_to_its_cases(
    team: Team, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    viewer = await client_for(team.viewer)
    assert await _status(viewer, team.case) == (200, 200), "control"
    response = await viewer.post(f"/v1/organisations/{team.org}/leave")
    assert response.status_code in (200, 204), f"{response.status_code} {_error(response)}"
    after = await _status(viewer, team.case)
    assert after == (404, 404), f"a person who left still reaches the case ({after})"


async def test_h_a_case_reassigned_takes_the_content_from_the_old_named_expert(
    team: Team, client_for: ClientFor, admin: psycopg.Connection, seed: Seed
) -> None:
    new_expert = seed.person()
    seed.membership(team.org, new_expert, "expert")
    old = await client_for(team.expert)
    assert await _status(old, team.case) == (200, 200), "control"
    owner = await client_for(team.owner)
    response = await owner.post(f"/v1/cases/{team.case}/named-expert", json={"person_id": str(new_expert)})
    assert response.status_code == 200, f"{response.status_code} {_error(response)}"
    assert admin.execute("select named_expert_id from app.cases where id = %s", (team.case,)).fetchone() == (
        new_expert,
    )
    after = await _status(old, team.case)
    print(f"AUDIT-11 reassigned: the old named expert (still an organisation expert) sees {after}")
    assert after[1] == 404, f"the old named expert still reads the case content ({after})"
    assert (await old.post(f"/v1/cases/{team.case}/process", json={})).status_code == 404
    assert await _status(await client_for(new_expert), team.case) == (200, 200), "control: the new expert has it"


async def test_h_an_organisation_that_closes_takes_its_members_off_its_cases(
    team: Team, client_for: ClientFor, admin: psycopg.Connection, seed: Seed
) -> None:
    seed.case_status(team.case, "served")
    assistant, org_admin = await client_for(team.assistant), await client_for(team.admin_p)
    assert await _status(assistant, team.case) == (200, 200), "control"
    response = await (await client_for(team.owner)).post(f"/v1/organisations/{team.org}/close")
    assert response.status_code == 204, f"{response.status_code} {_error(response)}"
    after = {name: await _status(c, team.case) for name, c in (("assistant", assistant), ("admin", org_admin))}
    expert = await _status(await client_for(team.expert), team.case)
    print(f"AUDIT-11 organisation closed: after={after}, named expert={expert}")
    assert after == {"assistant": (404, 404), "admin": (404, 404)}, after


async def test_h_a_closed_account_reads_only_its_served_cases_and_changes_nothing(
    client_for: ClientFor, admin: psycopg.Connection, seed: Seed
) -> None:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    served = seed.case(org, expert, status="served")
    unserved = seed.case(org, expert, status="active")
    client = await client_for(expert)
    assert await _status(client, served) == (200, 200) and await _status(client, unserved) == (200, 200), "control"
    seed.close_account(expert)
    on_served, on_unserved = await _status(client, served), await _status(client, unserved)
    me = (await client.get("/v1/me")).status_code
    write = (
        await client.post(
            f"/v1/cases/{served}/transcripts", json={"context": "opinion", "text": "zz", "duration_seconds": 1}
        )
    ).status_code
    queue = (await client.post(f"/v1/cases/{unserved}/process", json={})).status_code
    print(
        f"AUDIT-11 closed account: served case {on_served}, unserved case {on_unserved}, GET /v1/me {me}, "
        f"write on served {write}, queue a job on unserved {queue}"
    )
    assert on_unserved == (404, 404), "a closed account still reaches a case that is not served"
    assert write in (403, 404) and queue == 404, "a closed account can still change things"


async def test_h_a_revoked_session_is_refused_on_every_route_on_the_next_request(
    team: Team, client_for: ClientFor, admin: psycopg.Connection
) -> None:
    client = await client_for(team.expert)
    assert await _status(client, team.case) == (200, 200), "control"
    response = await (await client_for(team.expert)).post("/v1/me/sessions/revoke-all")
    assert response.status_code == 204, f"{response.status_code} {_error(response)}"
    for path in (f"/v1/cases/{team.case}", f"/v1/cases/{team.case}/pages", f"/v1/jobs/{team.job}", "/v1/me"):
        assert (await client.get(path)).status_code == 401, path
    assert (await client.post(f"/v1/cases/{team.case}/process", json={})).status_code in (401, 403)


@pytest.mark.parametrize("how", ["expired", "revoked"])
async def test_h_a_support_grant_that_expires_or_is_revoked_ends_support_access(
    how: str, team: Team, client_for: ClientFor, admin: psycopg.Connection, seed: Seed
) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    client = await client_for(staff)
    assert (await client.get(f"/v1/cases/{team.case}")).status_code == 404, "no grant, no access"
    grant = seed.support_grant(team.case, team.expert, granted_to=staff)
    assert (await client.get(f"/v1/cases/{team.case}")).status_code == 200, "control: a live grant opens the case"
    if how == "expired":
        admin.execute(
            "update app.support_access_grants set expires_at = now() - interval '1 second' where id = %s", (grant,)
        )
    else:
        response = await (await client_for(team.expert)).delete(f"/v1/cases/{team.case}/support-access/{grant}")
        assert response.status_code == 204, f"{response.status_code} {_error(response)}"
    assert (await client.get(f"/v1/cases/{team.case}")).status_code == 404
    assert (await client.get(f"/v1/cases/{team.case}/pages")).status_code == 404


async def test_h_an_invite_that_was_cancelled_cannot_be_accepted(
    team: Team, app: FastAPI, client_for: ClientFor, admin: psycopg.Connection, seed: Seed
) -> None:
    invitee = seed.person()
    email = f"{invitee.hex[:12]}@example.test"
    owner = await client_for(team.owner)
    sent = await owner.post(f"/v1/organisations/{team.org}/invites", json={"email": email, "role": "viewer"})
    assert sent.status_code == 201, sent.text
    mails = [m for m in app.state.mailer.outbox if m.template == ORG_INVITE]
    token = mails[-1].params["link"].rsplit("/", 1)[1]
    cancelled = await owner.delete(f"/v1/organisations/{team.org}/invites/{sent.json()['id']}")
    assert cancelled.status_code in (200, 204), f"{cancelled.status_code} {_error(cancelled)}"
    response = await (await client_for(invitee)).post(f"/v1/invites/{token}/accept")
    assert response.status_code in (404, 409, 410), f"{response.status_code} {_error(response)}"
    assert (
        _count(
            admin,
            "select count(*) from app.memberships where organisation_id = %s and person_id = %s",
            team.org,
            invitee,
        )
        == 0
    )
    assert await _status(await client_for(invitee), team.case) == (404, 404)


async def test_h_an_accepted_invite_gives_exactly_the_invited_role_and_cannot_be_used_twice(
    team: Team, app: FastAPI, client_for: ClientFor, admin: psycopg.Connection, seed: Seed
) -> None:
    invitee, other = seed.person(), seed.person()
    email = f"{invitee.hex[:12]}@example.test"
    owner = await client_for(team.owner)
    assert (
        await owner.post(f"/v1/organisations/{team.org}/invites", json={"email": email, "role": "viewer"})
    ).status_code == 201
    token = [m for m in app.state.mailer.outbox if m.template == ORG_INVITE][-1].params["link"].rsplit("/", 1)[1]
    stranger = await (await client_for(other)).post(f"/v1/invites/{token}/accept")
    assert stranger.status_code == 404, "someone else's session cannot use the token"
    assert (
        _count(
            admin, "select count(*) from app.memberships where organisation_id = %s and person_id = %s", team.org, other
        )
        == 0
    )
    accepted = await (await client_for(invitee)).post(f"/v1/invites/{token}/accept")
    assert accepted.status_code == 200, accepted.text
    row = admin.execute(
        "select role from app.memberships where organisation_id = %s and person_id = %s and ended_at is null",
        (team.org, invitee),
    ).fetchone()
    assert row == ("viewer",), "control"
    # a viewer who is only an organisation member (not on the case) does not see the case
    assert await _status(await client_for(invitee), team.case) == (404, 404)


def test_zz_the_measured_lags_are_recorded() -> None:
    """A summary line for the report (run with -s); the lags themselves were printed by the tests above.
    The lags are module state: when the tests that measure them did not run in this process (a `-k` filter, or
    another xdist worker), there is nothing to check and the test is skipped, never passed."""
    if len(LAGS) < _LAGS_EXPECTED:
        pytest.skip(f"only {len(LAGS)} of {_LAGS_EXPECTED} lag measurements were taken in this process")
    for name, lag in sorted(LAGS.items()):
        print(f"AUDIT-11 LAG SUMMARY {name}: {lag:.2f} s")
        assert 0 <= lag < 30, f"{name}: {lag:.2f} s"
