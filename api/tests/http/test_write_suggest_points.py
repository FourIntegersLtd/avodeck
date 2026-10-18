"""T-14a: sort the facts for one question by side (architecture 6.3, slice E brief).

`POST .../questions/{q_id}/points/suggest` queues `suggest_points` (202). The job sends the question
and the citable facts linked to it that are not yet points, each under a short reference, through the
AI kit (identifiers out first, rule 7); the side codes come from the pack. An answer naming a fact
that was not supplied is refused and asked once more; a second wrong answer adds nothing. Kept
answers are stored as suggested points with a short reason; a later run never changes a point the
expert has decided and sends only facts not yet points.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.features.write import ai_tasks
from app.features.write.jobs import suggest_points
from app.features.write.services import WriteServices, configure
from tests.fixtures.write.world import PLAN, RESULT, Reads, World, app_for, build
from tests.support.ai import CELL_ENV, NAME, cell
from tests.support.contracts import add_fact
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SEEN = f"seen by {NAME}"


class Answering:
    """A model stand-in that answers each request with `answer(request)` and keeps every request."""

    def __init__(self, *answers: Callable[[ModelRequest], dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return ModelResponse(output=answer(request), input_tokens=100, output_tokens=10)


class Names:
    async def case_names(self, conn: Any, case_id: uuid.UUID) -> list[str]:
        return [NAME]


def ref(request: ModelRequest, value: str) -> str:
    """The short reference the request gives the fact whose words are `value`."""
    lines = [line for line in request.text.splitlines() if line.endswith(value)]
    assert len(lines) == 1, "each fact once"
    return lines[0].split("\t")[0]


def fact_lines(request: ModelRequest) -> list[str]:
    return [line for line in request.text.splitlines() if line.startswith("f") and "\t" in line]


def sorted_by_side(request: ModelRequest) -> dict[str, Any]:
    return {
        "points": [
            {"fact": ref(request, PLAN), "side": "supports_clinician", "reason": "dated plan to review"},
            {"fact": ref(request, RESULT), "side": "supports_claim", "reason": "low result"},
        ]
    }


def unknown_fact(request: ModelRequest) -> dict[str, Any]:
    return {"points": [{"fact": "f99", "side": "neutral", "reason": "not supplied"}]}


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    built = build(seed, admin, combo, reads)
    add_fact(admin, built.case, built.read_page, status="verified", value=SEEN)
    return built


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def model(reads: Reads) -> Iterator[Callable[..., Answering]]:
    def use(*answers: Callable[[ModelRequest], dict[str, Any]]) -> Answering:
        answering = Answering(*answers)
        configure(WriteServices(kit=AiKit(cell(), answering), reads=reads, names=Names()))
        return answering

    yield use
    configure(None)


async def queue(client_for: ClientFor, w: World) -> uuid.UUID:
    queued = await (await client_for(w.expert)).post(w.url(f"questions/{w.question}/points/suggest"))
    assert queued.status_code == 202, queued.text
    return uuid.UUID(queued.json()["job_id"])


async def run(worker_db: Database, w: World, job_id: uuid.UUID) -> None:
    await suggest_points(JobContext(worker_db, job_id, w.case, "suggest_points", 1))


def stored(admin: psycopg.Connection, w: World) -> dict[uuid.UUID, tuple[Any, ...]]:
    rows = admin.execute(
        "select fact_id, side, status, origin, reason, model_task, prompt_version, job_id from app.points"
        " where question_id = %s",
        (w.question,),
    ).fetchall()
    return {r[0]: tuple(r[1:]) for r in rows}


async def test_only_the_named_expert_asks_for_points_to_be_sorted(
    client_for: ClientFor, world: World, model: Callable[..., Answering]
) -> None:
    model(sorted_by_side)
    await queue(client_for, world)
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer):
        got = await (await client_for(person)).post(world.url(f"questions/{world.question}/points/suggest"))
        assert got.status_code == 403
    outsider = await (await client_for(world.outsider)).post(world.url(f"questions/{world.question}/points/suggest"))
    assert outsider.status_code == 404
    unknown = await (await client_for(world.expert)).post(world.url(f"questions/{uuid.uuid4()}/points/suggest"))
    assert unknown.status_code == 404


async def test_the_model_sees_only_citable_facts_with_identifiers_out_and_the_packs_sides(
    client_for: ClientFor,
    world: World,
    admin: psycopg.Connection,
    worker_db: Database,
    combo: Combo,
    model: Callable[..., Answering],
) -> None:
    answering = model(sorted_by_side)
    job = await queue(client_for, world)
    await run(worker_db, world, job)
    [request] = answering.requests
    assert len(fact_lines(request)) == 3  # verified, edited and the one naming a person; never suggested,
    assert NAME not in request.text and "seen by" in request.text  # rejected or on an unread page
    sides = default_library().combine(*combo.codes).point_sides()
    assert request.context["point_sides"] == sides
    task = ai_tasks.t14a(default_library().combine(*combo.codes), ["f1"])
    assert task.task_id == "T-14a" and task.model == "sonnet"
    version = task.prompt_version
    assert stored(admin, world) == {
        world.verified: ("supports_clinician", "suggested", "ai", "dated plan to review", "T-14a", version, job),
        world.result: ("supports_claim", "suggested", "ai", "low result", "T-14a", version, job),
    }


async def test_an_answer_naming_a_fact_not_supplied_is_refused_and_asked_once_more(
    client_for: ClientFor,
    world: World,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    answering = model(unknown_fact, sorted_by_side)
    await run(worker_db, world, await queue(client_for, world))
    assert len(answering.requests) == 2
    assert {v[1] for v in stored(admin, world).values()} == {"suggested"} and len(stored(admin, world)) == 2


async def test_two_wrong_answers_add_no_points(
    client_for: ClientFor,
    world: World,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    answering = model(unknown_fact)
    await run(worker_db, world, await queue(client_for, world))
    assert len(answering.requests) == 2
    assert stored(admin, world) == {}
    outcomes = admin.execute(
        "select outcome from app.ai_calls where case_id = %s and task_id = 'T-14a'", (world.case,)
    ).fetchall()
    assert [o[0] for o in outcomes] == ["invalid_output", "invalid_output"]


async def test_a_later_run_sends_only_new_facts_and_leaves_decided_points_alone(
    client_for: ClientFor,
    world: World,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    def everything_neutral(request: ModelRequest) -> dict[str, Any]:
        refs = [line.split("\t")[0] for line in fact_lines(request)]
        return {"points": [{"fact": r, "side": "neutral", "reason": "second look"} for r in refs]}

    answering = model(sorted_by_side, everything_neutral)
    await run(worker_db, world, await queue(client_for, world))
    expert = await client_for(world.expert)
    point = admin.execute("select id from app.points where fact_id = %s", (world.verified,)).fetchone()
    assert point is not None
    accepted = await expert.patch(
        world.url(f"points/{point[0]}"), json={"status": "accepted"}, headers={"If-Match": '"1"'}
    )
    assert accepted.status_code == 200
    late = add_fact(admin, world.case, world.read_page, status="verified", value="safety netting advice given")
    await run(worker_db, world, await queue(client_for, world))
    assert len(answering.requests) == 2
    assert len(fact_lines(answering.requests[1])) == 2  # the one naming a person and the new one
    after = stored(admin, world)
    assert after[world.verified][:2] == ("supports_clinician", "accepted")
    assert after[late][:2] == ("neutral", "suggested")
