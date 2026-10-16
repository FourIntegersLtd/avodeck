"""Dictation relay and saved transcripts (architecture 5.3 T-DICT; foundation brief).

The browser streams microphone audio to FastAPI over a WebSocket; FastAPI relays it to the cell's
speech service (here the recorded stand-in) with the case's pack locale and phrase list, and sends
the text back. No audio is stored and no speech key or endpoint reaches the browser. The finished,
edited text is saved with POST /v1/cases/{case_id}/transcripts, encrypted under the case's key.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import psycopg
import pytest
from fastapi import FastAPI

from app.core.ai.recordings import FixtureStore
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.speech import RecordedSpeech, TranscriptSegment
from tests.support.ai import CELL_ENV, cell
from tests.support.auth import cookie_header
from tests.support.db import ClientFor, scope_for
from tests.support.files import build_app
from tests.support.seed import FIXTURE_PACKS, Seed
from tests.support.ws import ws_connect

pytestmark = pytest.mark.anyio

ORIGIN = "https://app.test"
AUDIO = [b"synthetic-audio-frame-1" * 40, b"synthetic-audio-frame-2" * 40]
SEGMENTS = [TranscriptSegment("synthetic words one", 0, 900), TranscriptSegment("synthetic words two", 900, 1800)]


@pytest.fixture
def recordings(tmp_path: Path) -> Path:
    return tmp_path / "speech"


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], recordings: Path, crypto: CaseCrypto) -> FastAPI:
    speech = RecordedSpeech(cell(), FixtureStore(recordings))
    app = build_app(api_db, auth_env, speech=speech, crypto=crypto)
    pack = app.state.packs.combine(
        FIXTURE_PACKS["jurisdiction"], FIXTURE_PACKS["case_type"], FIXTURE_PACKS["specialty"]
    )
    speech.record(b"".join(AUDIO), **pack.speech(), segments=SEGMENTS)
    return app


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "viewer", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "owner"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["viewer"]: "viewer"})
    return p


def headers(seed: Seed, person: uuid.UUID, *, origin: str | None = ORIGIN, **shape: object) -> list[tuple[str, str]]:
    found = [("cookie", cookie_header(seed.session(person, **shape)))]  # type: ignore[arg-type]
    return found + ([("origin", origin)] if origin else [])


def url(case: uuid.UUID, context: str = "opinion") -> str:
    return f"/v1/cases/{case}/dictation?context={context}"


async def test_audio_streamed_over_the_websocket_comes_back_as_text(
    app: FastAPI, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    async with ws_connect(app, url(team["case"]), headers(seed, team["expert"])) as ws:
        assert ws.accepted, ws.close_code
        for frame in AUDIO:
            await ws.send_bytes(frame)
        await ws.send_json({"type": "end"})
        messages = await ws.receive_until_closed()
    assert ws.close_code == 1000
    assert messages == [
        {"type": "segment", "text": "synthetic words one", "start_ms": 0, "end_ms": 900},
        {"type": "segment", "text": "synthetic words two", "start_ms": 900, "end_ms": 1800},
        {"type": "done"},
    ]


async def test_no_audio_is_stored_and_no_speech_key_or_endpoint_reaches_the_browser(
    app: FastAPI, seed: Seed, team: dict[str, uuid.UUID], recordings: Path, admin: psycopg.Connection
) -> None:
    before = sorted(p.read_bytes() for p in recordings.rglob("*") if p.is_file())
    async with ws_connect(app, url(team["case"]), headers(seed, team["expert"])) as ws:
        for frame in AUDIO:
            await ws.send_bytes(frame)
        await ws.send_json({"type": "end"})
        messages = await ws.receive_until_closed()
    sent = json.dumps(messages).lower()
    for secret in (CELL_ENV["AZURE_SPEECH_ENDPOINT"], "uksouth", "microsoft", "key", "token", "subscription"):
        assert secret.lower() not in sent
    after = sorted(p.read_bytes() for p in recordings.rglob("*") if p.is_file())
    assert after == before, "the relay wrote something to disk"
    for frame in AUDIO:
        assert all(frame not in blob for blob in after)
    assert admin.execute("select count(*) from app.transcripts where case_id = %s", (team["case"],)).fetchone() == (0,)


async def test_audio_the_speech_service_cannot_take_ends_with_a_code_not_an_exception(
    app: FastAPI, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    async with ws_connect(app, url(team["case"]), headers(seed, team["expert"])) as ws:
        await ws.send_bytes(b"synthetic audio with no recording")
        await ws.send_json({"type": "end"})
        messages = await ws.receive_until_closed()
    assert messages == [{"type": "error", "code": "speech_unavailable"}]
    assert ws.close_code == 1011


@pytest.mark.parametrize(
    ("who", "shape", "origin", "code"),
    [
        ("nobody", {}, ORIGIN, 4401),
        ("outsider", {}, ORIGIN, 4404),
        ("viewer", {}, ORIGIN, 4403),
        ("expert", {"aal": "aal1", "mfa_age": None}, ORIGIN, 4403),
        ("expert", {}, "https://elsewhere.example", 4403),
        ("expert", {}, None, 4403),
    ],
    ids=["signed-out", "outsider", "viewer", "mfa-not-done", "other-origin", "no-origin"],
)
async def test_the_relay_refuses_before_accepting(
    app: FastAPI,
    seed: Seed,
    team: dict[str, uuid.UUID],
    who: str,
    shape: dict[str, object],
    origin: str | None,
    code: int,
) -> None:
    sent = [("origin", origin)] if origin else []
    if who != "nobody":
        sent = headers(seed, team[who], origin=origin, **shape)
    async with ws_connect(app, url(team["case"]), sent) as ws:
        assert not ws.accepted
        assert ws.close_code == code


async def test_the_relay_refuses_a_context_that_is_not_a_short_code(
    app: FastAPI, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    async with ws_connect(app, url(team["case"], "free%20text%20here"), headers(seed, team["expert"])) as ws:
        assert not ws.accepted
        assert ws.close_code == 4422


async def test_a_saved_transcript_is_stored_encrypted_and_audited(
    client_for: ClientFor,
    team: dict[str, uuid.UUID],
    admin: psycopg.Connection,
    api_db: Database,
    crypto: CaseCrypto,
) -> None:
    text = "Synthetic dictated words, edited by the expert."
    resp = await (await client_for(team["expert"])).post(
        f"/v1/cases/{team['case']}/transcripts", json={"context": "opinion", "text": text, "duration_seconds": 42}
    )
    assert resp.status_code == 201, resp.text
    transcript = uuid.UUID(resp.json()["id"])
    row = admin.execute(
        "select case_id, person_id, context, duration_seconds, text from app.transcripts where id = %s", (transcript,)
    ).fetchone()
    assert row is not None
    assert row[:4] == (team["case"], team["expert"], "opinion", 42)
    assert text.encode() not in bytes(row[4])
    async with api_db.request(scope_for(team["expert"])) as conn:
        assert (await crypto.decrypt(conn, team["case"], bytes(row[4]))).decode() == text
    audit = admin.execute(
        "select action, target from app.audit_log where case_id = %s order by seq desc limit 1", (team["case"],)
    ).fetchone()
    assert audit == ("transcript.saved", f"transcript:{transcript}")


@pytest.mark.parametrize(
    "body",
    [
        {"context": "free text here", "text": "x", "duration_seconds": 1},
        {"context": "opinion", "text": "", "duration_seconds": 1},
        {"context": "opinion", "text": "x", "duration_seconds": -1},
    ],
)
async def test_a_transcript_needs_a_context_code_some_text_and_a_duration(
    client_for: ClientFor, team: dict[str, uuid.UUID], body: dict[str, object]
) -> None:
    resp = await (await client_for(team["expert"])).post(f"/v1/cases/{team['case']}/transcripts", json=body)
    assert resp.status_code == 422, resp.text


async def test_a_viewer_cannot_save_a_transcript(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    resp = await (await client_for(team["viewer"])).post(
        f"/v1/cases/{team['case']}/transcripts", json={"context": "opinion", "text": "x", "duration_seconds": 1}
    )
    assert resp.status_code == 403, resp.text
