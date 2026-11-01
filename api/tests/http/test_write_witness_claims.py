"""T-14c: what a witness statement says, set beside the records (architecture 6.3, slice E brief).

`POST .../witness-statements/{doc_id}/compare` queues `compare_witness_statement` (202) for one of the
case's documents. The job sends the statement's words (read from the pipeline's OCR words, identifiers
out first, rule 7) and the citable facts, each under a short reference, to the larger model. Each claim
it returns must be an exact run of the statement's words: the server finds those words and keeps them,
with their word IDs, and drops a claim that is not in the statement. Verdicts name only facts supplied;
an answer naming another is asked once more, and a second wrong answer adds nothing. Claims show on
screen 14 beside the facts they were compared with; the named expert confirms, gives their own verdict
or dismisses each, and the decision is kept.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.features.pipeline.paths import word_purpose
from app.features.write import ai_tasks
from app.features.write.jobs import compare_witness_statement
from app.features.write.services import WriteServices, configure
from tests.fixtures.write.world import PLAN, RESULT, Reads, World, app_for, build
from tests.support.ai import CELL_ENV, NAME, cell
from tests.support.contracts import add_fact
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

# Paragraph 3 of the synthetic witness statement (evals/generator/extras.py), planted against the
# record's "review 3/52 if no better" (page 118).
CLAIM = "I was never told to come back."
STATEMENT = f"I saw {NAME} on 15 May 2024. She started me on iron tablets. {CLAIM} I thought it would pass."
SEEN = f"seen by {NAME}"
CODES: dict[uuid.UUID, tuple[str, str, str]] = {}  # each test case's pack codes


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


def fact_lines(request: ModelRequest) -> list[str]:
    return [line for line in request.text.splitlines() if line.startswith("f") and "\t" in line]


def ref(request: ModelRequest, value: str) -> str:
    [line] = [line for line in fact_lines(request) if line.endswith(value)]
    return line.split("\t")[0]


def contradiction(request: ModelRequest) -> dict[str, Any]:
    return {
        "claims": [{"text_verbatim": "i was  never told to come back"}, {"text_verbatim": "I was told to come daily."}],
        "verdicts": [
            {"claim_index": 0, "fact_ids": [ref(request, PLAN)], "verdict": "contradicts"},
            {"claim_index": 1, "fact_ids": [], "verdict": "not_addressed"},
        ],
    }


def unknown_fact(request: ModelRequest) -> dict[str, Any]:
    return {
        "claims": [{"text_verbatim": CLAIM}],
        "verdicts": [{"claim_index": 0, "fact_ids": ["f99"], "verdict": "supports"}],
    }


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
    CODES[built.case] = (combo.jurisdiction, combo.case_type, combo.specialty)
    return built


@pytest.fixture
async def statement(seed: Seed, world: World, app: FastAPI, admin_dsn: str) -> tuple[uuid.UUID, list[str]]:
    """A witness statement on page 900: its words sealed as the pipeline seals them. Returns the
    document and the word references, `<page_id>/<word_id>`, in reading order."""
    document = seed.document(world.case)
    page = seed.page(world.case, document, 900)
    statement_type = sorted(default_library().combine(*CODES[world.case]).statement_sources())[0]
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
        await conn.execute("update app.pages set source_type = %s where id = %s", (statement_type, page))
    refs = []
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
        for n, word in enumerate(STATEMENT.split(), 1):
            sealed = await app.state.crypto.encrypt(
                conn, world.case, word.encode(), purpose=word_purpose(page, f"w{n}")
            )
            await conn.execute(
                "insert into app.ocr_words (page_id, bundle_id, n, word_id, text, polygon, confidence, source)"
                " values (%s, (select bundle_id from app.cases where id = %s), %s, %s, %s, "
                "array[0,0,1,0,1,1,0,1]::real[], 0.99, 'ocr')",
                (page, world.case, n, f"w{n}", sealed),
            )
            refs.append(f"{page}/w{n}")
    return document, refs


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
        configure(WriteServices(kit=AiKit(cell(), answering), crypto=app.state.crypto, reads=reads, names=Names()))
        return answering

    yield use
    configure(None)


async def compare(client_for: ClientFor, worker_db: Database, w: World, document: uuid.UUID) -> uuid.UUID:
    queued = await (await client_for(w.expert)).post(w.url(f"witness-statements/{document}/compare"))
    assert queued.status_code == 202, queued.text
    job = uuid.UUID(queued.json()["job_id"])
    await compare_witness_statement(JobContext(worker_db, job, w.case, "compare_witness_statement", 1))
    return job


async def claims(client_for: ClientFor, w: World, person: uuid.UUID | None = None) -> list[dict[str, Any]]:
    got = await (await client_for(person or w.expert)).get(w.url(f"questions/{w.question}/points"))
    assert got.status_code == 200, got.text
    shown: list[dict[str, Any]] = got.json()["claims"]
    return shown


async def test_only_the_named_expert_asks_for_a_statement_to_be_compared(
    client_for: ClientFor, world: World, statement: tuple[uuid.UUID, list[str]], model: Callable[..., Answering]
) -> None:
    model(contradiction)
    document, _ = statement
    path = world.url(f"witness-statements/{document}/compare")
    for person in (world.co_expert, world.supervisor, world.assistant, world.viewer):
        assert (await (await client_for(person)).post(path)).status_code == 403
    assert (await (await client_for(world.outsider)).post(path)).status_code == 404
    unknown = await (await client_for(world.expert)).post(world.url(f"witness-statements/{uuid.uuid4()}/compare"))
    assert unknown.status_code == 404
    assert (await (await client_for(world.expert)).post(path)).status_code == 202


async def test_the_model_sees_the_statement_and_the_citable_facts_with_identifiers_out(
    client_for: ClientFor,
    world: World,
    statement: tuple[uuid.UUID, list[str]],
    worker_db: Database,
    combo: Combo,
    model: Callable[..., Answering],
) -> None:
    answering = model(contradiction)
    await compare(client_for, worker_db, world, statement[0])
    [request] = answering.requests
    assert NAME not in request.text and CLAIM in request.text
    lines = fact_lines(request)
    assert len(lines) == 3  # verified, edited and the one naming a person; never suggested, rejected or unread
    assert any(line.endswith(PLAN) for line in lines) and any(line.endswith(RESULT) for line in lines)
    task = ai_tasks.t14c(default_library().combine(*combo.codes), ["f1"])
    assert task.task_id == "T-14c" and task.model == "opus"


async def test_a_claim_keeps_the_statements_exact_words_and_one_not_in_it_is_dropped(
    client_for: ClientFor,
    world: World,
    statement: tuple[uuid.UUID, list[str]],
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    model(contradiction)
    document, refs = statement
    job = await compare(client_for, worker_db, world, document)
    start = STATEMENT.split().index("never") - 2
    stored = admin.execute(
        "select claim_index, word_ids, verdict, fact_ids, status, model_task, prompt_version, job_id"
        " from app.witness_claims where document_id = %s",
        (document,),
    ).fetchall()
    assert stored == [
        (
            0,
            refs[start : start + 7],
            "contradicts",
            [world.verified],
            "suggested",
            "T-14c",
            ai_tasks.prompt("t14c")[0],
            job,
        )
    ]
    for person in (world.expert, world.co_expert, world.viewer):
        [shown] = await claims(client_for, world, person)
        assert shown["text_verbatim"] == CLAIM and shown["document_id"] == str(document)
        assert shown["verdict"] == "contradicts" and shown["status"] == "suggested" and shown["version"] == 1
        assert [(f["id"], f["value"], f["page_no"]) for f in shown["facts"]] == [(str(world.verified), PLAN, 1)]


async def test_the_expert_confirms_gives_their_own_verdict_or_dismisses_and_it_is_kept(
    client_for: ClientFor,
    world: World,
    statement: tuple[uuid.UUID, list[str]],
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    model(contradiction)
    await compare(client_for, worker_db, world, statement[0])
    [shown] = await claims(client_for, world)
    path = world.url(f"witness-claims/{shown['id']}")
    for person in (world.co_expert, world.assistant, world.viewer):
        got = await (await client_for(person)).patch(path, json={"status": "confirmed"}, headers={"If-Match": '"1"'})
        assert got.status_code == 403
    outsider = await (await client_for(world.outsider)).patch(
        path, json={"status": "confirmed"}, headers={"If-Match": '"1"'}
    )
    assert outsider.status_code == 404
    expert = await client_for(world.expert)
    assert (await expert.patch(path, json={"status": "confirmed"})).status_code == 428
    assert (await expert.patch(path, json={"status": "confirmed"}, headers={"If-Match": '"9"'})).status_code == 412
    no_verdict = await expert.patch(path, json={"status": "edited"}, headers={"If-Match": '"1"'})
    assert no_verdict.status_code == 422
    edited = await expert.patch(
        path, json={"status": "edited", "expert_verdict": "not_addressed"}, headers={"If-Match": '"1"'}
    )
    assert edited.status_code == 200 and edited.headers["ETag"] == '"2"'
    assert edited.json()["status"] == "edited" and edited.json()["expert_verdict"] == "not_addressed"
    assert edited.json()["text_verbatim"] == CLAIM
    again = await expert.patch(path, json={"status": "dismissed"}, headers={"If-Match": '"2"'})
    assert again.status_code == 409  # decided: only a change to a fact reopens it
    decided = admin.execute(
        "select status, expert_verdict, decided_by from app.witness_claims where id = %s", (shown["id"],)
    ).fetchone()
    assert decided == ("edited", "not_addressed", world.expert)
    audited = admin.execute(
        "select action, target from app.audit_log where case_id = %s and action like 'witness_claim.%%' order by seq",
        (world.case,),
    ).fetchall()
    assert audited == [("witness_claim.edited", f"witness_claim:{shown['id']}")]
    unknown = await expert.patch(
        world.url(f"witness-claims/{uuid.uuid4()}"), json={"status": "confirmed"}, headers={"If-Match": '"1"'}
    )
    assert unknown.status_code == 404


async def test_a_dismissed_claim_is_no_longer_shown(
    client_for: ClientFor,
    world: World,
    statement: tuple[uuid.UUID, list[str]],
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    model(contradiction)
    await compare(client_for, worker_db, world, statement[0])
    [shown] = await claims(client_for, world)
    dismissed = await (await client_for(world.expert)).patch(
        world.url(f"witness-claims/{shown['id']}"), json={"status": "dismissed"}, headers={"If-Match": '"1"'}
    )
    assert dismissed.status_code == 200
    assert await claims(client_for, world) == []


async def test_comparing_the_same_statement_again_adds_nothing(
    client_for: ClientFor,
    world: World,
    statement: tuple[uuid.UUID, list[str]],
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    answering = model(contradiction)
    await compare(client_for, worker_db, world, statement[0])
    await compare(client_for, worker_db, world, statement[0])
    assert len(answering.requests) == 1
    count = admin.execute("select count(*) from app.witness_claims where case_id = %s", (world.case,)).fetchone()
    assert count == (1,)


async def test_a_verdict_naming_a_fact_not_supplied_is_asked_once_more_then_nothing_is_kept(
    client_for: ClientFor,
    world: World,
    statement: tuple[uuid.UUID, list[str]],
    admin: psycopg.Connection,
    worker_db: Database,
    model: Callable[..., Answering],
) -> None:
    answering = model(unknown_fact)
    await compare(client_for, worker_db, world, statement[0])
    assert len(answering.requests) == 2
    assert await claims(client_for, world) == []
    outcomes = admin.execute(
        "select outcome from app.ai_calls where case_id = %s and task_id = 'T-14c'", (world.case,)
    ).fetchall()
    assert [o[0] for o in outcomes] == ["invalid_output", "invalid_output"]
    asked = admin.execute(
        "select status from app.witness_comparisons where document_id = %s", (statement[0],)
    ).fetchall()
    assert asked == [("refused",)]


async def test_a_document_that_is_not_a_witness_statement_is_refused(
    client_for: ClientFor, world: World, model: Callable[..., Answering]
) -> None:
    answering = model(contradiction)
    refused = await (await client_for(world.expert)).post(world.url(f"witness-statements/{world.document}/compare"))
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "not_a_statement"
    assert answering.requests == []


class Births:
    async def dates_of_birth(self, conn: Any, case_id: uuid.UUID) -> list[date]:
        return [date(2024, 5, 15)]


async def test_the_subjects_date_of_birth_is_taken_out_before_the_model_sees_the_statement(
    client_for: ClientFor,
    worker_db: Database,
    world: World,
    app: FastAPI,
    reads: Reads,
    statement: tuple[uuid.UUID, list[str]],
) -> None:
    answering = Answering(contradiction)
    configure(
        WriteServices(
            kit=AiKit(cell(), answering), crypto=app.state.crypto, reads=reads, names=Names(), births=Births()
        )
    )
    try:
        await compare(client_for, worker_db, world, statement[0])
    finally:
        configure(None)
    [request] = answering.requests
    assert "15 May 2024" not in request.text
    assert CLAIM in request.text
