"""Slice F, the final check (screen 17): `POST .../report/final-check` queues `final_check` (202); the job
runs the core checks plus the case's pack checks (`final_checks.yaml`) on the latest draft and T-17 over the
expert's own paragraphs, and stores one `final_check_runs` row. `GET .../report/final-check/latest` shows it.

Acceptance (slice F brief): on the synthetic case, one planted uncited date, one unread page and one
undecided finding fail exactly those three checks, each with a "Fix it" target (screen and item); the
statement wording check fails if the draft's wording differs from the pack's by one word; a cited fact
that changed since it was cited fails (architecture 10.4). T-17 gives warnings only and never blocks.
F34: a corrected declaration blocks until the expert acknowledges it. Results hold keys and ids only.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from app.features.report.assemble import PARAGRAPH_PURPOSE
from app.features.report.jobs import build_report, final_check
from app.features.report.services import ReportServices, configure
from tests.fixtures.report.world import (
    CLEAN,
    CRYPTO,
    UNCITED_DATE,
    Reads,
    World,
    app_for,
    build,
    make_ready,
    ready_refs,
    save_opinion,
)
from tests.support.ai import CELL_ENV, cell
from tests.support.contracts import add_question
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CORE = [
    "report_built",
    "draft_current",
    "questions_locked",
    "citations_resolve",
    "facts_unchanged_since_cited",
    "quotes_verbatim",
    "all_pages_read",
    "findings_decided",
    "dates_cited",
    "no_provisional_opinions",
    "sections_written",
    "lint_dismissals_explained",
    "conflicts_declared",
    "expert_details_complete",
    "signature_ready",
    "declaration_acknowledged",
    "age_consistent",
]
NOT_RUN = {"age_consistent"}  # no date of birth reaches slice F yet (brief TODO)


class Answering:
    def __init__(self, answer: Callable[[ModelRequest], dict[str, Any]]) -> None:
        self.answer = answer
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(output=self.answer(request), input_tokens=100, output_tokens=10)


def quiet(request: ModelRequest) -> dict[str, Any]:
    return {"issues": []}


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: SupabaseStorage) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads, storage)


@pytest.fixture
def model(reads: Reads, storage: SupabaseStorage) -> Iterator[Callable[..., Answering]]:
    def use(answer: Callable[[ModelRequest], dict[str, Any]] = quiet, *, kit: bool = True) -> Answering:
        answering = Answering(answer)
        configure(
            ReportServices(crypto=CRYPTO, storage=storage, reads=reads, kit=AiKit(cell(), answering) if kit else None)
        )
        return answering

    use()
    yield use
    configure(None)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


async def build_now(client_for: ClientFor, worker_db: Database, w: World) -> None:
    queued = await (await client_for(w.expert)).post(w.url("report/build"))
    assert queued.status_code == 202, queued.text
    await build_report(JobContext(worker_db, uuid.UUID(queued.json()["job_id"]), w.case, "build_report", 1))


async def check_now(client_for: ClientFor, worker_db: Database, w: World, person: uuid.UUID | None = None) -> Any:
    client = await client_for(person or w.expert)
    queued = await client.post(w.url("report/final-check"))
    assert queued.status_code == 202, queued.text
    await final_check(JobContext(worker_db, uuid.UUID(queued.json()["job_id"]), w.case, "final_check", 1))
    latest = await client.get(w.url("report/final-check/latest"))
    assert latest.status_code == 200, latest.text
    return latest.json()


def pack_keys(combo: Combo) -> list[str]:
    return [c["key"] for c in default_library().combine(*combo.codes).final_checks()]


def failed(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in run["results"] if r["outcome"] == "failed"}


@pytest.mark.usefixtures("model")
async def test_a_ready_case_passes_every_check_and_records_the_pack_versions(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, world)
    run = await check_now(client_for, worker_db, world)

    assert [r["key"] for r in run["results"]] == CORE + pack_keys(world.combo)
    assert failed(run) == {}
    assert {r["key"] for r in run["results"] if r["outcome"] == "not_run"} == NOT_RUN
    assert run["passed"] is True and run["current"] is True
    assert run["warnings"] == [] and run["warnings_status"] == "done"
    pack = default_library().combine(*world.combo.codes)
    assert run["pack_versions"] == pack.versions
    version = admin.execute(
        "select id from app.report_versions where case_id = %s order by version_no desc limit 1", (world.case,)
    ).fetchone()
    assert version is not None and run["report_version_id"] == str(version[0])
    for result in run["results"]:
        if result["source"] == "pack":
            assert result["text"], "pack checks carry the pack's own wording"


@pytest.mark.usefixtures("model")
async def test_an_uncited_date_an_unread_page_and_an_undecided_finding_fail_exactly_those_three(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    finding = await make_ready(
        seed, admin, aconn, world, reads, read_all=False, decided=False, body=f"{CLEAN}\n\n{UNCITED_DATE}"
    )
    await build_now(client_for, worker_db, world)
    run = await check_now(client_for, worker_db, world)

    problems = failed(run)
    assert set(problems) == {"dates_cited", "all_pages_read", "findings_decided"}
    assert run["passed"] is False
    assert problems["dates_cited"]["fix"] == {"screen": "S15", "item": str(world.question)}
    assert problems["all_pages_read"]["fix"] == {"screen": "S09", "item": "3"}
    assert problems["findings_decided"]["fix"] == {"screen": "S13", "item": str(finding)}
    for problem in problems.values():
        assert problem["count"] == 1 and problem["fixes"] == [problem["fix"]]
    stored = admin.execute(
        "select results::text from app.final_check_runs where case_id = %s", (world.case,)
    ).fetchone()
    assert stored is not None and "March" not in stored[0], "results hold keys and ids, never report words"


@pytest.mark.usefixtures("model")
async def test_a_cited_fact_that_changed_since_it_was_cited_fails(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, world)
    admin.execute("update app.facts set value = 'Ferritin 90 ug/L' where id = %s", (world.result,))
    run = await check_now(client_for, worker_db, world)

    problems = failed(run)
    assert set(problems) == {"facts_unchanged_since_cited"}
    assert problems["facts_unchanged_since_cited"]["fix"] == {"screen": "S15", "item": str(world.question)}


@pytest.mark.parametrize("combo", [LAUNCH], ids=["launch"])
@pytest.mark.usefixtures("model")
async def test_the_statement_wording_check_fails_when_the_draft_differs_from_the_pack_by_one_word(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, world)
    row = admin.execute(
        "select p.id, p.text from app.report_paragraphs p join app.report_versions v on v.id = p.report_version_id"
        " where p.case_id = %s and p.section = 'declarations' order by v.version_no desc, p.para_no limit 1",
        (world.case,),
    ).fetchone()
    assert row is not None
    text = (await CRYPTO.decrypt(aconn, world.case, row[1], purpose=PARAGRAPH_PURPOSE)).decode()
    words = text.split()
    words[len(words) // 2] = "zzword"
    sealed = await CRYPTO.encrypt(aconn, world.case, " ".join(words).encode(), purpose=PARAGRAPH_PURPOSE)
    admin.execute("update app.report_paragraphs set text = %s where id = %s", (sealed, row[0]))

    problems = failed(await check_now(client_for, worker_db, world))
    assert set(problems) == {"statement_of_truth_exact"}
    assert problems["statement_of_truth_exact"]["fix"] == {"screen": "S16", "item": "declarations"}
    settings = await (await client_for(world.expert)).get(world.url("report/settings"))
    assert "declarations" not in {
        s["key"]
        for s in (await (await client_for(world.expert)).get(world.url("report"))).json()["sections"]
        if s["written"]
    }
    assert settings.status_code == 200
    refused = await (await client_for(world.expert)).put(
        world.url("report/sections/declarations"), json={"body": "zz", "citation_refs": []}
    )
    assert refused.status_code == 422, "the expert cannot edit the declaration wording"


@pytest.mark.usefixtures("model")
async def test_a_corrected_declaration_blocks_until_the_expert_acknowledges_it(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    admin.execute(
        "update app.report_settings set acknowledged_declaration_version = 'zz-old' where case_id = %s", (world.case,)
    )
    await build_now(client_for, worker_db, world)
    run = await check_now(client_for, worker_db, world)
    problems = failed(run)
    assert set(problems) == {"declaration_acknowledged"}
    assert problems["declaration_acknowledged"]["fix"] == {"screen": "S18", "item": "declaration"}
    assert run["declaration_changed"] is True

    for person in (world.assistant, world.co_expert, world.viewer):
        assert (await (await client_for(person)).post(world.url("report/declarations/acknowledge"))).status_code == 403
    outsider = await client_for(world.outsider)
    assert (await outsider.post(world.url("report/declarations/acknowledge"))).status_code == 404
    done = await (await client_for(world.expert)).post(world.url("report/declarations/acknowledge"))
    assert done.status_code == 200, done.text
    assert done.json()["acknowledged"] and done.json()["acknowledged"] != "zz-old"

    run = await check_now(client_for, worker_db, world)
    assert failed(run) == {} and run["declaration_changed"] is False


@pytest.mark.usefixtures("model")
async def test_who_may_run_and_see_the_final_check(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    expert = await client_for(world.expert)
    assert (await expert.get(world.url("report/final-check/latest"))).status_code == 404
    not_built = await expert.post(world.url("report/final-check"))
    assert not_built.status_code == 409 and not_built.json()["error"]["code"] == "not_built"

    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, world)
    outsider = await client_for(world.outsider)
    assert (await outsider.post(world.url("report/final-check"))).status_code == 404
    assert (await outsider.get(world.url("report/final-check/latest"))).status_code == 404
    for person in (world.co_expert, world.viewer):
        assert (await (await client_for(person)).post(world.url("report/final-check"))).status_code == 403
    await check_now(client_for, worker_db, world, world.assistant)
    for person in (world.co_expert, world.viewer):
        assert (await (await client_for(person)).get(world.url("report/final-check/latest"))).status_code == 200


@pytest.mark.usefixtures("model")
async def test_a_draft_older_than_the_latest_opinion_change_is_not_current(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, world)
    await save_opinion(aconn, world.case, world.question, CLEAN + " Then reviewed.", ready_refs(world))
    admin.execute("update app.opinions set updated_at = now() + interval '1 second' where case_id = %s", (world.case,))
    problems = failed(await check_now(client_for, worker_db, world))
    assert set(problems) == {"draft_current"}
    assert problems["draft_current"]["fix"] == {"screen": "S16", "item": "build"}


@pytest.mark.parametrize("combo", [LAUNCH], ids=["launch"])
@pytest.mark.usefixtures("model")
async def test_the_page_limit_for_the_cases_track(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    track, limit = next(iter(LAUNCH.raw("jurisdiction", "report.yaml")["page_limit"].items()))
    await make_ready(seed, admin, aconn, world, reads)
    admin.execute("update app.cases set court_track = %s where id = %s", (track, world.case))
    await build_now(client_for, worker_db, world)
    admin.execute("update app.report_builds set page_count = %s where case_id = %s", (limit + 1, world.case))
    problems = failed(await check_now(client_for, worker_db, world))
    assert set(problems) == {"page_limit"}
    assert problems["page_limit"]["fix"] == {"screen": "S16", "item": "page_limit"}
    admin.execute("update app.report_builds set page_count = %s where case_id = %s", (limit, world.case))
    assert failed(await check_now(client_for, worker_db, world)) == {}


@pytest.mark.parametrize("combo", [LAUNCH], ids=["launch"])
@pytest.mark.usefixtures("model")
async def test_points_questions_and_guidance_come_from_the_pack_checks(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    guideline = default_library().combine(*LAUNCH.codes).guidelines()[0]
    before = date.fromisoformat(str(guideline["in_force_from"])) - timedelta(days=30)
    body = f"{CLEAN} Measured against {guideline['code']}."
    await make_ready(seed, admin, aconn, world, reads, body=body)
    admin.execute("update app.facts set event_date = %s where id = %s", (before, world.result))
    admin.execute(
        "insert into app.points (case_id, question_id, fact_id, side, origin, fact_version)"
        " values (%s, %s, %s, 'neutral', 'ai', 1)",
        (world.case, world.question, world.plan),
    )
    second = add_question(admin, world.case, number=2, status="confirmed")
    await build_now(client_for, worker_db, world)
    problems = failed(await check_now(client_for, worker_db, world))
    assert set(problems) == {"points_against_considered", "questions_answered", "guidance_in_force"}
    assert problems["points_against_considered"]["fix"] == {"screen": "S14", "item": str(world.question)}
    assert problems["questions_answered"]["fix"] == {"screen": "S15", "item": str(second)}
    assert problems["guidance_in_force"]["fix"] == {"screen": "S15", "item": str(world.question)}


async def test_tone_and_expertise_warnings_never_block_and_name_only_words_in_the_paragraph(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    model: Callable[..., Answering],
) -> None:
    def flag(request: ModelRequest) -> dict[str, Any]:
        lines = [line for line in request.text.splitlines() if line.startswith("sentence\t")]
        index = next(n for n, line in enumerate(lines) if "result was low" in line)
        return {
            "issues": [
                {"sentence_index": index, "type": "loaded_language", "words": "Result  was LOW"},
                {"sentence_index": index, "type": "outside_expertise", "words": "words that are not there"},
            ]
        }

    answering = model(flag)
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, world)
    run = await check_now(client_for, worker_db, world)

    assert run["passed"] is True and run["warnings_status"] == "done"
    assert len(answering.requests) == 1 and answering.requests[0].task_id == "T-17"
    assert [(w["type"], w["words"], w["section"]) for w in run["warnings"]] == [
        ("loaded_language", "result was low", "opinion")
    ]
    assert run["warnings"][0]["question_id"] == str(world.question)
    stored = admin.execute("select warnings::text from app.final_check_runs where case_id = %s", (world.case,))
    row = stored.fetchone()
    assert row is not None and "low" not in row[0], "stored warnings hold places, never words"

    model(kit=False)
    run = await check_now(client_for, worker_db, world)
    assert run["passed"] is True and run["warnings_status"] == "not_run" and run["warnings"] == []


@pytest.mark.usefixtures("model")
async def test_passed_checks_say_how_much_they_looked_at_and_pack_checks_explain_themselves(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, world)
    run = await check_now(client_for, worker_db, world)
    by_key = {r["key"]: r for r in run["results"]}

    cited = admin.execute(
        "select count(*), count(distinct fact_id) from app.citations c join app.report_versions v"
        " on v.id = c.report_version_id where v.case_id = %s",
        (world.case,),
    ).fetchone()
    assert cited is not None and cited[0] > 0
    assert (by_key["citations_resolve"]["checked"], by_key["citations_resolve"]["unit"]) == (cited[1], "fact")
    assert (by_key["quotes_verbatim"]["checked"], by_key["quotes_verbatim"]["unit"]) == (cited[0], "quote")
    unchanged = by_key["facts_unchanged_since_cited"]
    assert (unchanged["checked"], unchanged["total"], unchanged["unit"]) == (cited[0], cited[0], "fact")
    pages = admin.execute("select count(*) from app.pages where case_id = %s", (world.case,)).fetchone()
    assert pages is not None
    read = by_key["all_pages_read"]
    assert (read["checked"], read["total"], read["unit"]) == (pages[0], pages[0], "page")
    assert by_key["report_built"]["checked"] is None and by_key["report_built"]["unit"] is None
    for result in run["results"]:
        if result["source"] == "pack" and world.combo.codes[0] == "gb-eaw":
            assert result["explain"], "the launch pack words each check's explanation"


@pytest.mark.usefixtures("model")
async def test_the_run_is_grouped_by_outcome_with_a_count_and_a_ready_flag(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, world)
    run = await check_now(client_for, worker_db, world)
    assert run["ready"] is True and run["failed_count"] == 0 and run["failed"] == []
    assert {r["key"] for r in run["not_run"]} == NOT_RUN
    assert [r["key"] for r in run["passed_checks"]] == [r["key"] for r in run["results"] if r["outcome"] == "passed"]
    assert len(run["passed_checks"]) + len(run["not_run"]) == len(run["results"])


@pytest.mark.usefixtures("model")
async def test_a_failed_run_lists_the_failures_and_is_not_ready(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads, read_all=False, decided=False, body=f"{CLEAN}\n\n{UNCITED_DATE}")
    await build_now(client_for, worker_db, world)
    run = await check_now(client_for, worker_db, world)
    assert {r["key"] for r in run["failed"]} == {"dates_cited", "all_pages_read", "findings_decided"}
    assert run["failed_count"] == 3 and run["ready"] is False
    assert all(r["outcome"] == "passed" for r in run["passed_checks"])
