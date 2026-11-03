"""F5: when the primary model's on-demand quota is spent, the kit tries the cell's in-region overflow
models (`BEDROCK_OVERFLOW_MODEL_IDS`) before it backs off. Each model has its own quota bucket.

Nothing is retried outside the region (rule 5): overflow IDs pass the same in-region check as the
primary, and a model that was not configured is refused by `check_model`."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Mapping

import psycopg
import pytest

from app.core.ai.bedrock import ModelRequest, ModelResponse, ModelUnavailable, ServiceBusy
from app.core.ai.cell import ModelNotAllowed, check_model
from app.core.ai.kit import AiKit, AiTask, backoff_delays
from app.core.config import ConfigError, ai_settings
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.packs import MergedPack, default_library
from tests.support.ai import CELL_ENV, cell, cell_env, response
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PRIMARY = CELL_ENV["BEDROCK_SONNET_MODEL_ID"]
OVER_A = "anthropic.claude-overflow-a-test-v1:0"
OVER_B = "anthropic.claude-overflow-b-test-v1:0"
TASK = AiTask(
    task_id="T-overflow",
    prompt_version="2026-11-03.1",
    model="sonnet",
    system="Label each line with one of the allowed labels.",
    schema={
        "type": "object",
        "properties": {"labels": {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}},
        "required": ["labels"],
        "additionalProperties": False,
    },
)


class PerModelBedrock:
    """A model stand-in whose answer depends on the model ID asked for."""

    def __init__(self, steps: Mapping[str, ModelResponse | Exception]) -> None:
        self.steps = steps
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        step = self.steps[request.model_id]
        if isinstance(step, Exception):
            raise step
        return step

    @property
    def asked(self) -> list[str]:
        return [r.model_id for r in self.requests]


def kit_for(model: PerModelBedrock, sleeps: list[float], overflow: str = f"{OVER_A},{OVER_B}") -> AiKit:
    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    return AiKit(cell(BEDROCK_OVERFLOW_MODEL_IDS=overflow), model, sleep=fake_sleep)  # type: ignore[arg-type]


def request_for(model_id: str = PRIMARY) -> ModelRequest:
    return ModelRequest(model_id=model_id, system="s", context={}, text="t", schema=TASK.schema)


@pytest.fixture
def pack() -> MergedPack:
    return default_library().combine(*LAUNCH.codes)


@pytest.fixture
def case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    return expert, seed.case(org, expert, packs=LAUNCH.case_row())


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=6)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


# --- settings and the cell allow-list ------------------------------------------------------------------------------


def test_no_overflow_models_by_default() -> None:
    assert ai_settings(cell_env()).overflow_models == ()
    assert ai_settings(cell_env(BEDROCK_OVERFLOW_MODEL_IDS="")).overflow_models == ()
    assert ai_settings(cell_env(BEDROCK_OVERFLOW_MODEL_IDS=" , ,")).overflow_models == ()


def test_overflow_models_are_read_in_order_and_blanks_ignored() -> None:
    env = cell_env(BEDROCK_OVERFLOW_MODEL_IDS=f" {OVER_A}, ,{OVER_B} ,")
    assert ai_settings(env).overflow_models == (OVER_A, OVER_B)


@pytest.mark.parametrize(
    "bad",
    [
        "eu.anthropic.claude-sonnet-5-test-v1:0",
        "global.anthropic.claude-sonnet-5-test-v1:0",
        "arn:aws:bedrock:eu-west-2:000000000000:inference-profile/eu.anthropic.claude-sonnet-5-test-v1:0",
    ],
)
def test_an_overflow_model_that_is_not_in_region_is_refused(bad: str) -> None:
    with pytest.raises(ConfigError, match="BEDROCK_OVERFLOW_MODEL_IDS"):
        ai_settings(cell_env(BEDROCK_OVERFLOW_MODEL_IDS=f"{OVER_A},{bad}"))


def test_check_model_accepts_a_configured_overflow_model_and_nothing_else() -> None:
    settings = cell(BEDROCK_OVERFLOW_MODEL_IDS=OVER_A)
    assert check_model(settings, OVER_A) == OVER_A
    with pytest.raises(ModelNotAllowed):
        check_model(settings, OVER_B)
    with pytest.raises(ModelNotAllowed):
        check_model(cell(), OVER_A)


# --- the kit --------------------------------------------------------------------------------------------------------


async def test_a_busy_primary_falls_over_to_the_first_overflow_model_without_waiting() -> None:
    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: response({"labels": ["a"]}), OVER_B: ServiceBusy()})
    sleeps: list[float] = []
    out = await kit_for(model, sleeps)._invoke(request_for())
    assert out.output == {"labels": ["a"]}
    assert out.model_id == OVER_A
    assert model.asked == [PRIMARY, OVER_A]
    assert sleeps == []


async def test_an_unavailable_or_busy_overflow_model_is_skipped() -> None:
    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: ModelUnavailable(), OVER_B: response({"labels": ["b"]})})
    sleeps: list[float] = []
    out = await kit_for(model, sleeps)._invoke(request_for())
    assert out.model_id == OVER_B
    assert model.asked == [PRIMARY, OVER_A, OVER_B]
    assert sleeps == []

    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: ServiceBusy(), OVER_B: response({"labels": ["b"]})})
    assert (await kit_for(model, [])._invoke(request_for())).model_id == OVER_B


async def test_an_overflow_model_equal_to_the_request_model_is_not_asked_twice() -> None:
    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: response({"labels": ["a"]})})
    out = await kit_for(model, [], overflow=f"{PRIMARY},{OVER_A}")._invoke(request_for())
    assert out.model_id == OVER_A
    assert model.asked == [PRIMARY, OVER_A]


async def test_when_every_model_is_busy_the_existing_back_off_runs_on_the_primary() -> None:
    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: ServiceBusy(), OVER_B: ModelUnavailable()})
    sleeps: list[float] = []
    with pytest.raises(ProcessingPaused) as paused:
        await kit_for(model, sleeps)._invoke(request_for())
    assert paused.value.reason == "service_busy"
    assert sleeps == backoff_delays(4, 2.0)
    attempts = len(sleeps) + 1
    assert model.asked == [PRIMARY, OVER_A, OVER_B] * attempts


async def test_an_unavailable_primary_pauses_without_trying_overflow_models() -> None:
    model = PerModelBedrock({PRIMARY: ModelUnavailable(), OVER_A: response({"labels": ["a"]})})
    with pytest.raises(ProcessingPaused) as paused:
        await kit_for(model, [])._invoke(request_for())
    assert paused.value.reason == "model_unavailable"
    assert model.asked == [PRIMARY]


async def test_without_overflow_models_a_busy_primary_backs_off_exactly_as_before() -> None:
    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: response({"labels": ["a"]})})
    sleeps: list[float] = []
    with pytest.raises(ProcessingPaused) as paused:
        await kit_for(model, sleeps, overflow="")._invoke(request_for())
    assert paused.value.reason == "service_busy"
    assert sleeps == backoff_delays(4, 2.0)
    assert set(model.asked) == {PRIMARY}


async def test_the_detached_path_falls_over_too_and_logs_the_model_that_answered(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _, case_id = case
    job_id = seed.job(case_id)
    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: response({"labels": ["a"]}, 70, 5)})
    sleeps: list[float] = []
    out = await kit_for(model, sleeps).run_detached(
        worker_db, case_id, TASK, pack=pack, text="x", subject="t", job_id=job_id
    )
    assert out == {"labels": ["a"]}
    assert model.asked == [PRIMARY, OVER_A]
    rows = admin.execute("select model_id, outcome from app.ai_calls where case_id = %s", (case_id,)).fetchall()
    assert rows == [(OVER_A, "ok")]


async def test_detached_with_every_model_busy_pauses_with_the_back_off_schedule(
    worker_db: Database, seed: Seed, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    _, case_id = case
    job_id = seed.job(case_id)
    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: ServiceBusy()})
    sleeps: list[float] = []
    kit = kit_for(model, sleeps, overflow=OVER_A)
    kit.slot_poll_seconds = -1.0  # slot polls are recorded as -1.0 so they can be told apart from back-off waits
    with pytest.raises(ProcessingPaused) as paused:
        await kit.run_detached(worker_db, case_id, TASK, pack=pack, text="x", subject="t", job_id=job_id)
    assert paused.value.reason == "service_busy"
    assert [s for s in sleeps if s >= 0] == backoff_delays(4, 2.0)


async def test_a_call_through_a_connection_logs_the_overflow_model_that_answered(
    api_db: Database, admin: psycopg.Connection, pack: MergedPack, case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    expert, case_id = case
    model = PerModelBedrock({PRIMARY: ServiceBusy(), OVER_A: response({"labels": ["b"]}, 90, 4)})
    async with api_db.request(scope_for(expert)) as conn:
        out = await kit_for(model, []).run(conn, TASK, pack=pack, case_id=case_id, text="x")
    assert out == {"labels": ["b"]}
    rows = admin.execute(
        "select model_id, input_tokens, output_tokens, outcome from app.ai_calls where case_id = %s", (case_id,)
    ).fetchall()
    assert rows == [(OVER_A, 90, 4, "ok")]
