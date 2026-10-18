"""T-14b: citable facts the draft does not use yet (architecture 6.3, slice E brief).

`suggest_points` also looks at each question with a saved draft: it sends the draft (identifiers out
first, rule 7) and the citable facts linked to the question that the draft does not cite, each under a
short reference. The model picks the ones that bear on the draft, with a short label; an answer naming a
fact not supplied is refused and asked once more. Kept answers are shown on screen 14 as suggestions
the expert can dismiss; once the draft cites the fact, the suggestion is no longer shown.
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

# The planted unused fact: the safety-netting entry on page 117 of the synthetic bundle.
SAFETY = "Advised to call back or 111 if black stools, bleeding from back passage, breathless or worsening tiredness."
SEEN = f"seen by {NAME}"
DRAFT = f"Reviewed with {NAME}; a plan to review was recorded [[cite:1]]."


class Answering:
    """A model stand-in: T-14a sorts nothing, T-14b answers with `answers` in turn (the last repeats)."""

    def __init__(self, *answers: Callable[[ModelRequest], dict[str, Any]]) -> None:
        self.answers = list(answers)
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        if request.task_id != "T-14b":
            return ModelResponse(output={"points": []}, input_tokens=100, output_tokens=10)
        self.requests.append(request)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return ModelResponse(output=answer(request), input_tokens=100, output_tokens=10)


class Names:
    async def case_names(self, conn: Any, case_id: uuid.UUID) -> list[str]:
        return [NAME]


def fact_lines(request: ModelRequest) -> list[str]:
    return [line for line in request.text.splitlines() if line.startswith("f") and "\t" in line]


def ref(request: ModelRequest, value: str) -> str:
    [line] = [line for line in fact_lines(request) if line.endswith(value)]
    return line.split("\t")[0]


def safety_netting(request: ModelRequest) -> dict[str, Any]:
    return {"facts": [{"fact": ref(request, SAFETY), "why_relevant": "advice to return if worse"}]}


def unknown_fact(request: ModelRequest) -> dict[str, Any]:
    return {"facts": [{"fact": "f99", "why_relevant": "not supplied"}]}


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
def safety(world: World, admin: psycopg.Connection) -> uuid.UUID:
    return add_fact(admin, world.case, world.read_page, status="verified", value=SAFETY)


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
def model(app: FastAPI, reads: Reads) -> Iterator[Callable[..., Answering]]:
    def use(*answers: Callable[[ModelRequest], dict[str, Any]]) -> Answering:
        answering = Answering(*answers)
        kit = AiKit(cell(), answering)
        configure(WriteServices(kit=kit, crypto=app.state.crypto, reads=reads, names=Names()))
        return answering

    yield use
    configure(None)


def cite(fact: uuid.UUID, page: uuid.UUID, quote: str) -> dict[str, str]:
    return {"fact_id": str(fact), "page_id": str(page), "quote_verbatim": quote}


async def draft(client_for: ClientFor, w: World, etag: str = '"0"', **change: Any) -> str:
    body = {"body": DRAFT, "citation_refs": [cite(w.verified, w.read_page, PLAN)], "view": None, "provisional": False}
    saved = await (await client_for(w.expert)).put(
        w.url(f"questions/{w.question}/opinion"), json={**body, **change}, headers={"If-Match": etag}
    )
    assert saved.status_code == 200, saved.text
    return saved.headers["ETag"]


async def run(client_for: ClientFor, worker_db: Database, w: World) -> uuid.UUID:
    queued = await (await client_for(w.expert)).post(w.url(f"questions/{w.question}/points/suggest"))
    assert queued.status_code == 202, queued.text
    job = uuid.UUID(queued.json()["job_id"])
    await suggest_points(JobContext(worker_db, job, w.case, "suggest_points", 1))
    return job


async def points(client_for: ClientFor, w: World, person: uuid.UUID | None = None) -> dict[str, Any]:
    got = await (await client_for(person or w.expert)).get(w.url(f"questions/{w.question}/points"))
    assert got.status_code == 200, got.text
    body: dict[str, Any] = got.json()
    return body


async def test_no_draft_means_nothing_is_asked(
    client_for: ClientFor, world: World, safety: uuid.UUID, worker_db: Database, model: Callable[..., Answering]
) -> None:
    answering = model(safety_netting)
    await run(client_for, worker_db, world)
    assert answering.requests == []
    assert (await points(client_for, world))["relevant"] == []


async def test_the_model_sees_the_draft_and_only_the_facts_it_does_not_cite(
    client_for: ClientFor,
    world: World,
    safety: uuid.UUID,
    worker_db: Database,
    combo: Combo,
    model: Callable[..., Answering],
) -> None:
    answering = model(safety_netting)
    await draft(client_for, world)
    await run(client_for, worker_db, world)
    [request] = answering.requests
    assert NAME not in request.text and "a plan to review was recorded" in request.text
    lines = fact_lines(request)
    assert len(lines) == 3  # the result, the planted safety netting and the one naming a person
    assert not any(line.endswith(PLAN) for line in lines)  # already cited; suggested, rejected, unread never
    assert any(line.endswith(RESULT) for line in lines) and any(line.endswith(SAFETY) for line in lines)
    task = ai_tasks.t14b(default_library().combine(*combo.codes), ["f1"])
    assert task.task_id == "T-14b" and task.model == "sonnet"


async def test_the_planted_unused_fact_is_suggested_and_shown_to_everyone_on_the_case(
    client_for: ClientFor,
    world: World,
    safety: uuid.UUID,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    model(safety_netting)
    await draft(client_for, world)
    job = await run(client_for, worker_db, world)
    for person in (world.expert, world.co_expert, world.viewer):
        [shown] = (await points(client_for, world, person))["relevant"]
        assert shown["fact"]["id"] == str(safety) and shown["fact"]["page_no"] == 1
        assert shown["why_relevant"] == "advice to return if worse" and shown["version"] == 1
    stored = admin.execute(
        "select status, model_task, prompt_version, job_id from app.relevant_facts where fact_id = %s", (safety,)
    ).fetchone()
    assert stored == ("suggested", "T-14b", ai_tasks.prompt("t14b")[0], job)
    outsider = await (await client_for(world.outsider)).get(world.url(f"questions/{world.question}/points"))
    assert outsider.status_code == 404


async def test_a_suggestion_goes_once_the_draft_cites_the_fact_and_is_not_asked_again(
    client_for: ClientFor, world: World, safety: uuid.UUID, worker_db: Database, model: Callable[..., Answering]
) -> None:
    answering = model(safety_netting)
    etag = await draft(client_for, world)
    await run(client_for, worker_db, world)
    both = [cite(world.verified, world.read_page, PLAN), cite(safety, world.read_page, "call back or 111")]
    await draft(client_for, world, etag, body=DRAFT + " Advice was given [[cite:2]].", citation_refs=both)
    assert (await points(client_for, world))["relevant"] == []
    await run(client_for, worker_db, world)
    assert len(answering.requests) == 2
    assert not any(line.endswith(SAFETY) for line in fact_lines(answering.requests[1]))


async def test_only_the_named_expert_dismisses_a_suggestion_with_if_match_and_it_is_audited(
    client_for: ClientFor,
    world: World,
    safety: uuid.UUID,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    model(safety_netting)
    await draft(client_for, world)
    await run(client_for, worker_db, world)
    [shown] = (await points(client_for, world))["relevant"]
    path = world.url(f"relevant-facts/{shown['id']}")
    dismiss = {"status": "dismissed"}
    for person in (world.co_expert, world.assistant, world.viewer):
        got = await (await client_for(person)).patch(path, json=dismiss, headers={"If-Match": '"1"'})
        assert got.status_code == 403
    assert (
        await (await client_for(world.outsider)).patch(path, json=dismiss, headers={"If-Match": '"1"'})
    ).status_code == 404
    expert = await client_for(world.expert)
    assert (await expert.patch(path, json=dismiss)).status_code == 428
    assert (await expert.patch(path, json=dismiss, headers={"If-Match": '"7"'})).status_code == 412
    assert (await expert.patch(path, json={"status": "suggested"}, headers={"If-Match": '"1"'})).status_code == 422
    done = await expert.patch(path, json=dismiss, headers={"If-Match": '"1"'})
    assert done.status_code == 200 and done.headers["ETag"] == '"2"'
    assert done.json()["status"] == "dismissed"
    assert (await points(client_for, world))["relevant"] == []
    audited = admin.execute(
        "select action, target from app.audit_log where case_id = %s and action like 'relevant_fact.%%' order by seq",
        (world.case,),
    ).fetchall()
    assert audited == [("relevant_fact.dismissed", f"relevant_fact:{shown['id']}")]
    unknown = await expert.patch(world.url(f"relevant-facts/{uuid.uuid4()}"), json=dismiss, headers={"If-Match": '"1"'})
    assert unknown.status_code == 404


async def test_an_answer_naming_a_fact_not_supplied_is_asked_once_more_then_nothing_is_kept(
    client_for: ClientFor,
    world: World,
    safety: uuid.UUID,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    answering = model(unknown_fact, safety_netting)
    await draft(client_for, world)
    await run(client_for, worker_db, world)
    assert len(answering.requests) == 2
    assert [r["fact"]["id"] for r in (await points(client_for, world))["relevant"]] == [str(safety)]
    admin.execute("delete from app.relevant_facts where case_id = %s", (world.case,))
    twice = model(unknown_fact)
    await run(client_for, worker_db, world)
    assert len(twice.requests) == 2
    assert (await points(client_for, world))["relevant"] == []
    outcomes = admin.execute(
        "select outcome from app.ai_calls where case_id = %s and task_id = 'T-14b'", (world.case,)
    ).fetchall()
    assert sorted(o[0] for o in outcomes) == ["invalid_output", "invalid_output", "invalid_output", "ok"]
