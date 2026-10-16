"""Failure modes the AI kit owns (docs/spec/failure-modes.md): F5, F10, F11 and F24.

- F5: throttling or an outage backs off exponentially, then pauses processing (never drops work).
- F10: a synthetic canary proves identifier stripping still works, and a log scanner finds any
  identifier a pack recognises in log lines (reporting where, never what).
- F11: once a case's AI use passes 3x the expected amount (K3), calls stop and an alert is logged.
- F24: an unavailable in-region model fails closed; nothing falls back to another model or region.

F6 (worker crash) belongs to the jobs step and is tested there.
"""

from __future__ import annotations

import json
import logging
import uuid

import httpx
import psycopg
import pytest
from fastapi import APIRouter, FastAPI

from app.core import logs
from app.core.ai import pii
from app.core.ai.bedrock import ModelUnavailable, ServiceBusy
from app.core.ai.kit import AiKit, AiTask
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.packs import MergedPack, default_library
from app.main import mount
from tests.support.ai import FIXTURES, NAME, ScriptedBedrock, cell, mod11_number, response, spaced
from tests.support.db import scope_for
from tests.support.packs import FIXTURE, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

TASK = AiTask(
    task_id="T-test",
    prompt_version="2026-10-16.1",
    model="sonnet",
    system="Label each line.",
    schema={"type": "object", "properties": {"labels": {"type": "array"}}, "required": ["labels"]},
)
OK = response({"labels": []}, 25, 10)


class Sleeps(list[float]):
    async def __call__(self, seconds: float) -> None:
        self.append(seconds)


def merged(combo: Combo) -> MergedPack:
    return default_library().combine(*combo.codes)


@pytest.fixture
def case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    return expert, seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())


# --- F5: throttling and outages --------------------------------------------------------------------------------------


async def test_f5_a_busy_service_is_retried_with_growing_waits_then_processing_pauses(
    api_db: Database, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    sleeps = Sleeps()
    model = ScriptedBedrock([ServiceBusy()])
    with pytest.raises(ProcessingPaused) as paused:
        async with api_db.request(scope_for(expert)) as conn:
            await AiKit(cell(), model, sleep=sleeps, retries=4).run(
                conn, TASK, pack=merged(LAUNCH), case_id=case_id, text="x"
            )
    assert paused.value.reason == "service_busy"
    assert len(model.requests) == 5
    assert sleeps == sorted(sleeps) and len(set(sleeps)) == len(sleeps), "waits grow"
    assert sum(sleeps) <= 2 * 60 * 60


async def test_f5_a_busy_service_that_recovers_gives_the_answer_and_honours_retry_after(
    api_db: Database, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    sleeps = Sleeps()
    model = ScriptedBedrock([ServiceBusy(retry_after=30), ServiceBusy(), OK])
    async with api_db.request(scope_for(expert)) as conn:
        out = await AiKit(cell(), model, sleep=sleeps).run(conn, TASK, pack=merged(LAUNCH), case_id=case_id, text="x")
    assert out == {"labels": []}
    assert len(sleeps) == 2 and sleeps[0] >= 30


async def test_processing_paused_reaches_the_browser_as_503_with_its_reason(app: FastAPI) -> None:
    router = APIRouter()

    @router.get("/v1/test-only/paused")
    async def paused() -> None:
        raise ProcessingPaused("service_busy")

    mount(app, router)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:
        resp = await client.get("/v1/test-only/paused")
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "processing_paused"
    assert resp.json()["error"]["reason"] == "service_busy"


# --- F10: canary and log scanner -------------------------------------------------------------------------------------


def canaries() -> list[dict[str, object]]:
    return json.loads((FIXTURES / "canary.json").read_text())["canaries"]


@pytest.mark.parametrize("canary", canaries(), ids=lambda c: str(c["id"]))
def test_f10_the_synthetic_canary_is_fully_stripped(canary: dict[str, object]) -> None:
    combo = {"launch": LAUNCH, "fixture": FIXTURE}[str(canary["packs"])]
    pack = merged(combo)
    out = pii.strip(str(canary["text"]), pack=pack, names=list(canary.get("names", [])))  # type: ignore[call-overload]
    for value in canary["remove"]:  # type: ignore[attr-defined]
        assert str(value).lower() not in out.text.lower(), f"canary {canary['id']} leaked an identifier"
    for value in canary["keep"]:  # type: ignore[attr-defined]
        assert str(value) in out.text, f"canary {canary['id']} lost a value the model needs"
    assert logs.find_identifiers([out.text], pack.identifier_recognisers()) == []


def test_canaries_cover_every_pack_set() -> None:
    assert {c["packs"] for c in canaries()} == {"launch", "fixture"}


def test_f10_the_log_scanner_reports_where_an_identifier_is_never_what() -> None:
    number = mod11_number("943476591")
    pack = merged(LAUNCH)
    lines = ["GET /v1/cases 200 12ms", f"job step failed for {spaced(number)}", "ok"]
    findings = logs.find_identifiers(lines, pack.identifier_recognisers())
    assert [(f.line, f.kind) for f in findings] == [(1, findings[0].kind)]
    assert number not in repr(findings) and spaced(number) not in repr(findings)
    assert logs.find_identifiers(["ZZ-ID-123456"], merged(FIXTURE).identifier_recognisers())


# --- F11: cost guard -------------------------------------------------------------------------------------------------


async def test_f11_the_cost_guard_pauses_a_case_past_three_times_the_expected_use(
    api_db: Database, seed: Seed, admin: psycopg.Connection, case: tuple[uuid.UUID, uuid.UUID],
    caplog: pytest.LogCaptureFixture,
) -> None:
    expert, case_id = case
    document = seed.document(case_id)
    seed.page(case_id, document, 1)
    seed.page(case_id, document, 2)
    # Expected use 10 tokens a page, at least 1 page: this 2-page case may use 3 x 10 x 2 = 60 tokens.
    settings = cell(AI_EXPECTED_TOKENS_PER_PAGE="10", AI_MIN_EXPECTED_PAGES="1")
    model = ScriptedBedrock([OK])
    kit = AiKit(settings, model)
    for _ in range(2):  # 35 then 70 tokens used
        async with api_db.request(scope_for(expert)) as conn:
            await kit.run(conn, TASK, pack=merged(LAUNCH), case_id=case_id, text="x")

    caplog.set_level(logging.WARNING)
    with pytest.raises(ProcessingPaused) as paused:
        async with api_db.request(scope_for(expert)) as conn:
            await kit.run(conn, TASK, pack=merged(LAUNCH), case_id=case_id, text="x")
    assert paused.value.reason == "cost_guard"
    assert len(model.requests) == 2, "no call is sent once the guard trips"
    alerts = [r for r in caplog.records if r.levelno >= logging.ERROR and "cost_guard" in r.getMessage()]
    assert alerts, "an alert is raised"
    assert admin.execute("select count(*) from app.ai_calls where case_id = %s", (case_id,)).fetchone() == (2,)


async def test_f11_a_new_case_has_a_minimum_allowance(api_db: Database, case: tuple[uuid.UUID, uuid.UUID]) -> None:
    expert, case_id = case  # no pages yet: the minimum page count applies
    settings = cell(AI_EXPECTED_TOKENS_PER_PAGE="10", AI_MIN_EXPECTED_PAGES="3")
    async with api_db.request(scope_for(expert)) as conn:
        for _ in range(2):  # 35, 70 < 90
            await AiKit(settings, ScriptedBedrock([OK])).run(conn, TASK, pack=merged(LAUNCH), case_id=case_id, text="x")


# --- F24: fail closed ------------------------------------------------------------------------------------------------


async def test_f24_an_unavailable_model_fails_closed_without_falling_back(
    api_db: Database, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    sleeps = Sleeps()
    model = ScriptedBedrock([ModelUnavailable()])
    with pytest.raises(ProcessingPaused) as paused:
        async with api_db.request(scope_for(expert)) as conn:
            await AiKit(cell(), model, sleep=sleeps).run(conn, TASK, pack=merged(LAUNCH), case_id=case_id, text="x")
    assert paused.value.reason == "model_unavailable"
    assert [r.model_id for r in model.requests] == [cell().models["sonnet"]], "no other model or region was tried"
    assert sleeps == []


def test_no_name_from_the_canary_is_real() -> None:
    """The canary is synthetic: its names are the made-up test name only."""
    names = {n for c in canaries() for n in c.get("names", [])}  # type: ignore[attr-defined]
    assert names <= {NAME}
