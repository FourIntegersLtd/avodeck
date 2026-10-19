"""T-15a: the model's warnings on the opinion, and dismissing a warning (slice E brief; screen 15).

`POST .../questions/{q_id}/opinion/lint` sends the saved opinion, sentence by sentence and numbered,
with its citation quotes, to the model (identifiers out first, rule 7), with the specialty and the
question types the specialty pack marks as usually outside its expertise in the context. It answers in
real time (under 2 s at p95). Each warning names a sentence, a kind and the words flagged, and the words
must be in that sentence; a warning whose words are not there is dropped, and an answer naming a sentence
that does not exist is asked once more and then only the pattern check's warnings stay. A warning never
carries other words (rule 1). The named expert dismisses a warning with a reason; the dismissal is kept
for the final check and the same warning is not raised again.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.packs import default_library
from app.features.write import ai_tasks
from app.features.write.services import WriteServices, configure
from tests.fixtures.write.world import PLAN, Reads, World, app_for, build
from tests.support.ai import CELL_ENV, NAME, cell
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CAUSED = "caused the anaemia"
BODY = f"{NAME} was seen and the plan [[cite:1]] was safe. The delay {CAUSED} to worsen. Review was in 3 weeks."
KEYS = {"id", "sentence_index", "sentence_start", "sentence_end", "type", "words", "source", "status"}


class Answering:
    def __init__(self, answer: Callable[[ModelRequest], dict[str, Any]]) -> None:
        self.answer = answer
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(output=self.answer(request), input_tokens=100, output_tokens=10)


class Names:
    async def case_names(self, conn: Any, case_id: uuid.UUID) -> list[str]:
        return [NAME]


def causation(request: ModelRequest) -> dict[str, Any]:
    return {
        "issues": [
            {"sentence_index": 1, "type": "outside_expertise", "words": "Caused  the anaemia"},
            {"sentence_index": 0, "type": "outside_expertise", "words": "words that are not there"},
        ]
    }


def no_such_sentence(request: ModelRequest) -> dict[str, Any]:
    return {"issues": [{"sentence_index": 99, "type": "outside_expertise", "words": CAUSED}]}


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
    return build(seed, admin, combo, reads)


@pytest.fixture
def model(app: FastAPI, reads: Reads) -> Iterator[Callable[..., Answering]]:
    def use(answer: Callable[[ModelRequest], dict[str, Any]]) -> Answering:
        answering = Answering(answer)
        configure(WriteServices(kit=AiKit(cell(), answering), crypto=app.state.crypto, reads=reads, names=Names()))
        return answering

    yield use
    configure(None)


async def save(client: Any, w: World, etag: str = '"0"', body: str = BODY) -> dict[str, Any]:
    refs = (
        [{"fact_id": str(w.verified), "page_id": str(w.read_page), "quote_verbatim": PLAN}]
        if "[[cite:1]]" in body
        else []
    )
    payload = {"body": body, "citation_refs": refs, "view": None, "provisional": False}
    saved = await client.put(w.url(f"questions/{w.question}/opinion"), json=payload, headers={"If-Match": etag})
    assert saved.status_code == 200, saved.text
    out: dict[str, Any] = saved.json()
    return out


async def lint(client: Any, w: World) -> Any:
    return await client.post(w.url(f"questions/{w.question}/opinion/lint"))


def shown(issues: list[dict[str, Any]]) -> list[tuple[int, str, str, str]]:
    return sorted((i["sentence_index"], i["type"], i["words"], i["source"]) for i in issues)


async def test_only_the_named_expert_asks_and_only_once_there_is_a_saved_opinion(
    client_for: ClientFor, world: World, model: Callable[..., Answering]
) -> None:
    model(causation)
    expert = await client_for(world.expert)
    assert (await lint(expert, world)).status_code == 404
    await save(expert, world)
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer):
        assert (await lint(await client_for(person), world)).status_code == 403
    assert (await lint(await client_for(world.outsider), world)).status_code == 404
    assert (await lint(expert, world)).status_code == 200


async def test_the_model_sees_the_numbered_sentences_and_the_specialty_rule_with_identifiers_out(
    client_for: ClientFor, world: World, combo: Combo, model: Callable[..., Answering]
) -> None:
    answering = model(causation)
    expert = await client_for(world.expert)
    await save(expert, world)
    await lint(expert, world)
    [request] = answering.requests
    pack = default_library().combine(*combo.codes)
    assert request.context["outside_expertise_question_types"] == pack.outside_expertise_question_types()
    assert request.context["outside_expertise_question_types"]
    assert NAME not in request.text
    lines = request.text.splitlines()
    assert f"sentence\t1\tThe delay {CAUSED} to worsen." in lines
    assert f"citation\t1\t{PLAN}" in lines
    task = ai_tasks.t15a(pack, 3)
    assert task.task_id == "T-15a" and task.model == "sonnet"


async def test_a_warning_keeps_the_sentences_own_words_and_one_not_in_it_is_dropped(
    client_for: ClientFor, world: World, admin: psycopg.Connection, model: Callable[..., Answering]
) -> None:
    model(causation)
    expert = await client_for(world.expert)
    await save(expert, world)
    got = await lint(expert, world)
    assert got.status_code == 200, got.text
    issues = got.json()["issues"]
    assert shown(issues) == [(1, "outside_expertise", CAUSED, "ai"), (2, "uncited_fact", "3", "regex")]
    assert all(set(i) == KEYS and i["status"] == "open" for i in issues)
    again = await lint(expert, world)
    assert shown(again.json()["issues"]) == shown(issues)  # asking again adds nothing twice
    stored = admin.execute(
        "select source, opinion_version from app.lint_issues where case_id = %s and status = 'open' order by source",
        (world.case,),
    ).fetchall()
    assert stored == [("ai", 1), ("regex", 1)]


async def test_an_answer_naming_a_sentence_that_does_not_exist_is_asked_once_more_then_dropped(
    client_for: ClientFor, world: World, admin: psycopg.Connection, model: Callable[..., Answering]
) -> None:
    answering = model(no_such_sentence)
    expert = await client_for(world.expert)
    await save(expert, world)
    got = await lint(expert, world)
    assert got.status_code == 200
    assert len(answering.requests) == 2
    assert shown(got.json()["issues"]) == [(2, "uncited_fact", "3", "regex")]
    outcomes = admin.execute(
        "select outcome from app.ai_calls where case_id = %s and task_id = 'T-15a'", (world.case,)
    ).fetchall()
    assert [o[0] for o in outcomes] == ["invalid_output", "invalid_output"]


async def test_lint_answers_in_under_2_seconds_at_p95(
    client_for: ClientFor, world: World, model: Callable[..., Answering]
) -> None:
    model(causation)
    expert = await client_for(world.expert)
    await save(expert, world)
    times = []
    for _ in range(20):
        started = time.perf_counter()
        assert (await lint(expert, world)).status_code == 200
        times.append(time.perf_counter() - started)
    assert sorted(times)[18] < 2.0


async def test_the_named_expert_dismisses_a_warning_with_a_reason_and_it_is_kept(
    client_for: ClientFor, world: World, admin: psycopg.Connection, model: Callable[..., Answering]
) -> None:
    model(causation)
    expert = await client_for(world.expert)
    [issue] = (await save(expert, world))["lint"]
    path = world.url(f"lint-issues/{issue['id']}")
    reason = {"dismissed_reason": "the interval is in the plan"}
    for person in (world.co_expert, world.assistant, world.viewer):
        assert (await (await client_for(person)).patch(path, json=reason)).status_code == 403
    assert (await (await client_for(world.outsider)).patch(path, json=reason)).status_code == 404
    assert (await expert.patch(path, json={})).status_code == 422
    assert (await expert.patch(path, json={"dismissed_reason": ""})).status_code == 422
    unknown = await expert.patch(world.url(f"lint-issues/{uuid.uuid4()}"), json=reason)
    assert unknown.status_code == 404
    dismissed = await expert.patch(path, json=reason)
    assert dismissed.status_code == 200, dismissed.text
    assert set(dismissed.json()) == KEYS and dismissed.json()["status"] == "dismissed"
    assert (await expert.patch(path, json=reason)).status_code == 409
    kept = admin.execute(
        "select status, dismissed_by, dismissed_reason from app.lint_issues where id = %s", (issue["id"],)
    ).fetchone()
    assert kept is not None and kept[:2] == ("dismissed", world.expert)
    assert reason["dismissed_reason"].encode() not in bytes(kept[2])  # sealed under the case key
    audited = admin.execute(
        "select action, target from app.audit_log where case_id = %s and action like 'lint_issue.%%'", (world.case,)
    ).fetchall()
    assert audited == [("lint_issue.dismissed", f"lint_issue:{issue['id']}")]


async def test_a_dismissed_warning_is_not_raised_again(
    client_for: ClientFor, world: World, admin: psycopg.Connection, model: Callable[..., Answering]
) -> None:
    model(causation)
    expert = await client_for(world.expert)
    [issue] = (await save(expert, world))["lint"]
    await expert.patch(world.url(f"lint-issues/{issue['id']}"), json={"dismissed_reason": "in the plan"})
    assert (await save(expert, world, '"1"'))["lint"] == []
    assert shown((await lint(expert, world)).json()["issues"]) == [(1, "outside_expertise", CAUSED, "ai")]
    statuses = admin.execute(
        "select status from app.lint_issues where case_id = %s order by status", (world.case,)
    ).fetchall()
    assert statuses == [("dismissed",), ("open",)]


async def test_saving_keeps_the_models_warnings_whose_words_are_still_there_and_drops_the_rest(
    client_for: ClientFor, world: World, admin: psycopg.Connection, model: Callable[..., Answering]
) -> None:
    model(causation)
    expert = await client_for(world.expert)
    await save(expert, world)
    await lint(expert, world)
    kept = await save(expert, world, '"1"')
    assert shown(kept["lint"]) == [(1, "outside_expertise", CAUSED, "ai"), (2, "uncited_fact", "3", "regex")]
    gone = await save(expert, world, '"2"', BODY.replace(f" {CAUSED}", " led the anaemia"))
    assert shown(gone["lint"]) == [(2, "uncited_fact", "3", "regex")]
