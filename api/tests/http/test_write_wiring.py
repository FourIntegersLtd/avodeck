"""Slice E's services as the worker builds them from the cell (audit: worker AI tasks produced nothing).

A worker job runs without anyone calling `configure()`: the services come from the cell config, so the
read log is slice B's own (`page_reads`), the links are slice D's confirmed links, the facts flagged for
points are slice B's flags, the guidance is the overlay pack's, and the party names and statement words
come through the core contracts. Only the model client is a stand-in here (no cloud accounts yet).
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator, Iterator
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.features.write import services
from app.features.write.jobs import suggest_points
from tests.fixtures.write.world import PLAN, RESULT, Reads, World, build
from tests.support.ai import CELL_ENV
from tests.support.contracts import add_fact
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

UNLINKED = "not linked to the question"


class Answering:
    def __init__(self) -> None:
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        lines = [line for line in request.text.splitlines() if line.startswith("f") and "\t" in line]
        points = [{"fact": line.split("\t")[0], "side": "neutral", "reason": "dated entry"} for line in lines]
        return ModelResponse(output={"points": points}, input_tokens=100, output_tokens=10)


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def cell_model(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Answering]:
    """The cell's env with recorded answers, and the model client standing in for the recordings."""
    answering = Answering()
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.delenv(key, raising=False)  # live_or_recorded() prefers live Bedrock when these are set
    for key, value in CELL_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setenv("AI_RECORDINGS_DIR", str(tmp_path))
    monkeypatch.setenv("KEY_PROVIDER", "local")
    monkeypatch.setenv("LOCAL_MASTER_KEY", base64.b64encode(b"k" * 32).decode())
    monkeypatch.setattr(services, "RecordedBedrock", lambda settings, store: answering)
    monkeypatch.setattr(services, "_current", None)
    yield answering
    services.configure(None)


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo) -> World:
    built = build(seed, admin, combo, Reads())
    admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 20)",
        (built.case, built.read_page, built.expert),
    )
    for fact in (built.verified, built.result):
        admin.execute(
            "insert into app.fact_question_links (case_id, fact_id, question_id, source, status, decided_by)"
            " values (%s, %s, %s, 'expert', 'confirmed', %s)",
            (built.case, fact, built.question, built.expert),
        )
    add_fact(admin, built.case, built.read_page, status="verified", value=UNLINKED)
    return built


async def test_a_worker_job_without_configure_suggests_points_from_the_real_read_log_and_links(
    cell_model: Answering, worker_db: Database, seed: Seed, admin: psycopg.Connection, world: World
) -> None:
    job = seed.job(world.case, kind="suggest_points", requested_by=world.expert)
    await suggest_points(JobContext(worker_db, job, world.case, "suggest_points", 1))
    [request] = cell_model.requests
    assert PLAN in request.text and RESULT in request.text
    assert UNLINKED not in request.text, "only facts the expert linked to the question"
    rows = admin.execute("select fact_id from app.points where question_id = %s", (world.question,)).fetchall()
    assert {r[0] for r in rows} == {world.verified, world.result}


async def test_the_cell_services_flag_link_and_guide_from_the_other_slices(
    cell_model: Answering, worker_db: Database, admin: psycopg.Connection, world: World, combo: Combo
) -> None:
    admin.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, 'points', %s)",
        (world.case, world.result, world.expert),
    )
    built = services.current()
    pack = default_library().combine(combo.jurisdiction, combo.case_type, combo.specialty)
    async with worker_db.job(world.case) as conn:
        assert await built.flags.flagged_for_points(conn, world.case) == {world.result}
        assert await built.party_names().case_names(conn, world.case) == []
        on = date(2024, 5, 15)
        guidance: list[Any] = await built.guidance.in_force(conn, world.case, pack, on)
    expected = [
        str(g["code"])
        for g in pack.guidelines()
        if date.fromisoformat(str(g["in_force_from"])) <= on
        and (g.get("in_force_to") is None or on < date.fromisoformat(str(g["in_force_to"])))
    ]
    assert [g.code for g in guidance] == expected


def test_no_case_is_needed_to_build_the_cell_services(cell_model: Answering) -> None:
    built = services.from_env()
    assert built.kit is not None and built.crypto is not None and built.reads is not None
