"""Security audit 16 (document provenance and output integrity), slice F's findings, each proved by a test that
asserts the safe outcome (PV-nn in the name).

- PV-14 a citation to a page since set aside (confirmed copy, quarantined, replaced) or unreadable with no
  typing is not rendered (`[?]`), the final check names the page, and signing refuses it, in opinions and in the
  expert's own sections alike.
- PV-15 the final check says "not every page is read and readable" when a page cannot be read and has no typing,
  a page's reading failed, or the bundle is still being processed; set-aside pages are not counted as unread;
  the report's methodology appendix says how many pages were set aside and what was incomplete; a draft is
  still built; the chronology lists dated entries with how the date was known and states the undated ones.
- PV-12 every saved body of a section is kept, with who and when, and the save is audited without text.
- PV-18 `report_builds.job_id` joins the build to `jobs.code_version`.
- Rows of a locked version (paragraphs, citations, snapshots, build) cannot be changed or deleted; the case
  purge still removes them.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.report.assemble import SECTION_PURPOSE
from app.features.report.jobs import build_report, final_check
from app.features.report.services import ReportServices, configure
from app.features.report.words import word
from tests.fixtures.report.world import (
    CRYPTO,
    Reads,
    World,
    app_for,
    build,
    cite,
    make_ready,
    store_signature,
)
from tests.support.ai import CELL_ENV, cell
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SIGN = {"declaration_confirmed": True}
HOW = ["duplicate", "quarantined", "replaced"]


class Answering:
    def __init__(self) -> None:
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(output={"issues": []}, input_tokens=100, output_tokens=10)


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: CaseFileStorage) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads, storage)


@pytest.fixture(autouse=True)
def services(reads: Reads, storage: CaseFileStorage) -> Iterator[None]:
    configure(ReportServices(crypto=CRYPTO, storage=storage, reads=reads, kit=AiKit(cell(), Answering())))
    yield
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


def finished(admin: psycopg.Connection, job_id: str) -> None:
    admin.execute("update app.jobs set status = 'running' where id = %s", (job_id,))
    admin.execute("update app.jobs set status = 'done', finished_at = now() where id = %s", (job_id,))


async def build_now(client_for: ClientFor, worker_db: Database, admin: psycopg.Connection, w: World) -> str:
    queued = await (await client_for(w.expert)).post(w.url("report/build"))
    assert queued.status_code == 202, queued.text
    job = str(queued.json()["job_id"])
    await build_report(JobContext(worker_db, uuid.UUID(job), w.case, "build_report", 1))
    finished(admin, job)
    return job


async def check_now(client_for: ClientFor, worker_db: Database, admin: psycopg.Connection, w: World) -> Any:
    client = await client_for(w.expert)
    queued = await client.post(w.url("report/final-check"))
    assert queued.status_code == 202, queued.text
    await final_check(JobContext(worker_db, uuid.UUID(queued.json()["job_id"]), w.case, "final_check", 1))
    finished(admin, queued.json()["job_id"])
    latest = await client.get(w.url("report/final-check/latest"))
    assert latest.status_code == 200, latest.text
    return latest.json()


def failed(run: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["key"]: r for r in run["results"] if r["outcome"] == "failed"}


def writable(combo: Combo) -> str:
    keys = [s["key"] for s in default_library().combine(*combo.codes).report_sections()]
    return str(next(k for k in keys if k not in ("opinion", "declarations")))


async def paragraphs(client_for: ClientFor, w: World, section: str) -> list[str]:
    resp = await (await client_for(w.expert)).get(w.url(f"report/paragraphs?section={section}"))
    assert resp.status_code == 200, resp.text
    return [p["text"] for p in resp.json()["paragraphs"]]


def set_aside(admin: psycopg.Connection, w: World, index: int, how: str) -> None:
    """Page `index` (0-based) set aside the way the pipeline and the reader do it."""
    page = w.pages[index]
    other = w.pages[1 if index == 0 else 0]
    if how == "duplicate":
        admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (other, page))
        admin.execute(
            "insert into app.page_analysis (page_id, bundle_id, duplicate_status)"
            " select id, bundle_id, 'confirmed' from app.pages where id = %s"
            " on conflict (page_id) do update set duplicate_status = 'confirmed'",
            (page,),
        )
    elif how == "suggested":
        admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (other, page))
        admin.execute(
            "insert into app.page_analysis (page_id, bundle_id, duplicate_status)"
            " select id, bundle_id, 'suggested' from app.pages where id = %s"
            " on conflict (page_id) do update set duplicate_status = 'suggested'",
            (page,),
        )
    elif how == "quarantined":
        admin.execute("update app.pages set quarantined = true where id = %s", (page,))
    elif how == "replaced":
        admin.execute("update app.pages set superseded_by_page_id = %s where id = %s", (other, page))
    else:
        raise AssertionError(how)


async def cite_in_section(
    aconn: psycopg.AsyncConnection, w: World, key: str, fact: uuid.UUID, page: uuid.UUID, quote: str
) -> None:
    sealed = await CRYPTO.encrypt(aconn, w.case, b"Noted as set out [[cite:1]].", purpose=SECTION_PURPOSE)
    await aconn.execute(
        "update app.report_section_texts set body = %s, citation_refs = %s where case_id = %s and section = %s",
        (sealed, Jsonb([cite(fact, page, quote)]), w.case, key),
    )


# --- PV-14 -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("how", HOW)
async def test_pv14a_a_citation_to_a_page_set_aside_is_not_rendered(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    how: str,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    set_aside(admin, world, 0, how)
    await build_now(client_for, worker_db, admin, world)
    text = " ".join(await paragraphs(client_for, world, "opinion"))
    assert "[?]" in text and "[p.1]" not in text, "the cite to the set-aside page is not rendered"
    assert "[p.2]" in text, "a cite to a page still in the records is"
    cited = admin.execute(
        "select count(*) from app.citations where case_id = %s and fact_id = %s", (world.case, world.plan)
    ).fetchone()
    assert cited == (0,), "no citation row is kept for it"


async def test_pv14a_a_citation_to_an_unreadable_page_with_no_typing_is_not_rendered(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    admin.execute("update app.pages set unreadable = true where id = %s", (world.pages[0],))
    await build_now(client_for, worker_db, admin, world)
    text = " ".join(await paragraphs(client_for, world, "opinion"))
    assert "[?]" in text and "[p.1]" not in text


async def test_pv14a_control_a_page_only_suggested_as_a_copy_or_typed_by_the_expert_is_still_cited(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    set_aside(admin, world, 0, "suggested")
    admin.execute(
        "update app.pages set unreadable = true, transcribed_by_expert_text = %s where id = %s",
        (b"sealed", world.pages[1]),
    )
    await build_now(client_for, worker_db, admin, world)
    text = " ".join(await paragraphs(client_for, world, "opinion"))
    assert "[?]" not in text and "[p.1]" in text and "[p.2]" in text


async def test_pv14a_the_final_check_names_the_page_a_citation_depends_on_that_was_set_aside(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, admin, world)
    assert (await check_now(client_for, worker_db, admin, world))["passed"] is True
    set_aside(admin, world, 0, "duplicate")
    run = await check_now(client_for, worker_db, admin, world)
    problems = failed(run)
    assert "citations_resolve" in problems
    assert {"screen": "S08", "item": "1"} in problems["citations_resolve"]["fixes"], "the page is named"


async def test_pv14b_the_final_check_fails_a_section_citation_that_does_not_stand_at_build(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    key = writable(world.combo)
    await cite_in_section(aconn, world, key, world.unread, world.pages[2], "seen again")
    admin.execute("update app.facts set value = 'seen again in clinic and changed' where id = %s", (world.unread,))
    admin.execute("update app.facts set value = 'something else' where id = %s", (world.unread,))
    await build_now(client_for, worker_db, admin, world)
    assert "[?]" in " ".join(await paragraphs(client_for, world, key))
    run = await check_now(client_for, worker_db, admin, world)
    problems = failed(run)
    assert "facts_unchanged_since_cited" in problems, "a section citation that renders as [?] fails the check"
    assert {"screen": "S16", "item": key} in problems["facts_unchanged_since_cited"]["fixes"]


async def signed_ready(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    world: World,
    reads: Reads,
    *,
    section_cites: bool = False,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await store_signature(storage, admin, world)
    if section_cites:
        await cite_in_section(aconn, world, writable(world.combo), world.unread, world.pages[2], "seen again")
    await build_now(client_for, worker_db, admin, world)
    run = await check_now(client_for, worker_db, admin, world)
    assert run["passed"] is True, [r for r in run["results"] if r["outcome"] == "failed"]


async def test_pv14b_a_section_citation_that_stopped_standing_after_the_check_blocks_signing(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
) -> None:
    await signed_ready(client_for, worker_db, seed, admin, aconn, storage, world, reads, section_cites=True)
    admin.execute("update app.facts set value = 'something else entirely' where id = %s", (world.unread,))
    resp = await (await client_for(world.expert)).post(world.url("report/sign"), json=SIGN)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "citation_problems"
    row = admin.execute(
        "select count(*) from app.report_versions where case_id = %s and locked_at is not null", (world.case,)
    )
    assert row.fetchone() == (0,)


@pytest.mark.parametrize("where", ["opinion", "section"])
async def test_pv14a_a_citation_to_a_page_set_aside_after_the_check_blocks_signing(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    where: str,
) -> None:
    await signed_ready(
        client_for, worker_db, seed, admin, aconn, storage, world, reads, section_cites=where == "section"
    )
    set_aside(admin, world, 0 if where == "opinion" else 2, "duplicate")
    resp = await (await client_for(world.expert)).post(world.url("report/sign"), json=SIGN)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "citation_problems"


# --- PV-15 -------------------------------------------------------------------------------------------------


async def ready_check(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> Any:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, admin, world)
    return await check_now(client_for, worker_db, admin, world)


async def test_pv15_a_page_that_cannot_be_read_and_has_no_typing_fails_all_pages_read(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    admin.execute("update app.pages set unreadable = true where id = %s", (world.pages[2],))
    await build_now(client_for, worker_db, admin, world)
    problems = failed(await check_now(client_for, worker_db, admin, world))
    assert set(problems) == {"all_pages_read"}
    assert {"screen": "S10", "item": "3"} in problems["all_pages_read"]["fixes"]

    admin.execute("update app.pages set transcribed_by_expert_text = %s where id = %s", (b"sealed", world.pages[2]))
    assert failed(await check_now(client_for, worker_db, admin, world)) == {}, "typed by the expert: complete"


async def test_pv15_a_page_whose_reading_failed_and_has_no_typing_fails_all_pages_read(
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
        "insert into app.page_analysis (page_id, bundle_id, read_failure)"
        " select id, bundle_id, 'ocr_failed' from app.pages where id = %s",
        (world.pages[2],),
    )
    await build_now(client_for, worker_db, admin, world)
    problems = failed(await check_now(client_for, worker_db, admin, world))
    assert set(problems) == {"all_pages_read"}
    assert {"screen": "S10", "item": "3"} in problems["all_pages_read"]["fixes"]


async def test_pv15_bundle_processing_that_is_not_finished_fails_all_pages_read(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, admin, world)
    job = seed.job(world.case, kind="process_bundle")
    problems = failed(await check_now(client_for, worker_db, admin, world))
    assert set(problems) == {"all_pages_read"}
    assert {"screen": "S07", "item": "processing"} in problems["all_pages_read"]["fixes"]

    finished(admin, str(job))
    assert failed(await check_now(client_for, worker_db, admin, world)) == {}


async def test_pv15_a_processing_job_that_failed_for_good_does_not_block_when_every_page_is_read_and_checked(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, admin, world)
    job = seed.job(world.case, kind="process_pages")
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed', finished_at = now() where id = %s", (job,))
    assert failed(await check_now(client_for, worker_db, admin, world)) == {}, "failed is final: no way forward"


async def test_pv15_a_processing_job_that_failed_still_blocks_through_a_page_whose_reading_failed(
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
        "insert into app.page_analysis (page_id, bundle_id, read_failure)"
        " select id, bundle_id, 'ocr_failed' from app.pages where id = %s",
        (world.pages[2],),
    )
    await build_now(client_for, worker_db, admin, world)
    job = seed.job(world.case, kind="process_pages")
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'failed', finished_at = now() where id = %s", (job,))
    problems = failed(await check_now(client_for, worker_db, admin, world))
    assert set(problems) == {"all_pages_read"}
    fixes = problems["all_pages_read"]["fixes"]
    assert {"screen": "S10", "item": "3"} in fixes
    assert {"screen": "S07", "item": "processing"} not in fixes


@pytest.mark.parametrize(
    ("code", "still_going"),
    [("service_busy", True), ("resuming", True), ("cost_guard", False), ("cancelled", False)],
)
async def test_pv15_a_paused_job_is_open_only_while_the_worker_will_resume_it(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    code: str,
    still_going: bool,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    await build_now(client_for, worker_db, admin, world)
    job = seed.job(world.case, kind="process_bundle")
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'paused', error_code = %s where id = %s", (code, job))
    problems = failed(await check_now(client_for, worker_db, admin, world))
    assert ("all_pages_read" in problems) is still_going, code


async def test_pv15_a_page_set_aside_is_not_counted_as_unread_but_one_only_suggested_is(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads, read_all=False)
    await build_now(client_for, worker_db, admin, world)
    set_aside(admin, world, 2, "suggested")
    problems = failed(await check_now(client_for, worker_db, admin, world))
    assert {"screen": "S09", "item": "3"} in problems["all_pages_read"]["fixes"], "only a suggestion: still to read"

    set_aside(admin, world, 2, "duplicate")
    assert "all_pages_read" not in failed(await check_now(client_for, worker_db, admin, world))


async def test_pv15_the_methodology_appendix_says_how_many_pages_were_set_aside(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads, read_all=False)
    set_aside(admin, world, 2, "duplicate")
    body = (await (await client_for(world.expert)).get(world.url("report/methodology"))).json()
    assert (body["pages_read"], body["pages_total"], body["pages_set_aside"]) == (2, 2, 1)
    await build_now(client_for, worker_db, admin, world)
    text = await paragraphs(client_for, world, "methodology")
    assert word("en", "pages_read", read=2, total=2) in text
    assert word("en", "pages_set_aside", n=1) in text, "the reader of the report can see what was set aside"


async def test_pv15_a_draft_is_still_built_but_says_what_was_incomplete(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    admin.execute("update app.pages set unreadable = true where id = %s", (world.pages[2],))
    admin.execute("update app.documents set pages = 3, pages_declared = 5 where id = %s", (world.document,))
    seed.job(world.case, kind="process_bundle")
    await build_now(client_for, worker_db, admin, world)  # not blocked
    text = await paragraphs(client_for, world, "methodology")
    assert word("en", "incomplete_unreadable", n=1) in text
    assert word("en", "incomplete_processing") in text
    assert word("en", "incomplete_shortfall", n=1) in text
    body = (await (await client_for(world.expert)).get(world.url("report/methodology"))).json()
    assert set(body["incomplete"]) == {"unreadable_pages", "processing_open", "page_shortfall"}


async def test_pv15_a_page_shortfall_is_disclosed_and_does_not_block_the_final_check(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
) -> None:
    await make_ready(seed, admin, aconn, world, reads)
    admin.execute("update app.documents set pages = 3, pages_declared = 5 where id = %s", (world.document,))
    await build_now(client_for, worker_db, admin, world)
    run = await check_now(client_for, worker_db, admin, world)
    assert failed(run) == {} and run["passed"] is True
    assert word("en", "incomplete_shortfall", n=1) in await paragraphs(client_for, world, "methodology")


@pytest.mark.parametrize("combo", [LAUNCH], ids=["launch"])
async def test_pv15_the_chronology_shows_how_each_date_was_known_and_never_drops_an_undated_entry(
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
        "update app.facts set event_date = '2024-03-12', date_basis = 'expert_entered' where id = %s", (world.plan,)
    )
    admin.execute(
        "update app.facts set event_date = '2024-04-01', date_basis = 'printed' where id = %s", (world.result,)
    )
    await build_now(client_for, worker_db, admin, world)
    lines = await paragraphs(client_for, world, "chronology")
    entered = next(line for line in lines if line.startswith("12/03/2024"))
    printed = next(line for line in lines if line.startswith("01/04/2024"))
    assert word("en", "date_basis_expert_entered") in entered
    assert "(" not in printed, "a printed date reads as it always did"
    assert word("en", "undated_entries", n=1) in lines, "the count of entries with no date is stated"
    assert any(line.startswith("seen again in clinic") and line.endswith("[p.3]") for line in lines)


# --- PV-12 (the report part) -------------------------------------------------------------------------------


async def test_pv12_every_saved_body_of_a_section_is_kept_with_who_and_when_and_the_save_is_audited(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: World
) -> None:
    key = writable(world.combo)
    client = await client_for(world.expert)
    first = await client.put(world.url(f"report/sections/{key}"), json={"body": "First wording here.", "citations": []})
    assert first.status_code == 200, first.text
    second = await client.put(
        world.url(f"report/sections/{key}"),
        json={"body": "Second wording here.", "citations": []},
        headers={"If-Match": first.headers["etag"]},
    )
    assert second.status_code == 200, second.text
    rows = admin.execute(
        "select v.version_no, v.recorded_by, v.body, v.created_at from app.report_section_versions v"
        " join app.report_section_texts t on t.id = v.section_text_id where t.case_id = %s and t.section = %s"
        " order by v.version_no",
        (world.case, key),
    ).fetchall()
    assert [r[0] for r in rows] == [1, 2], "the first body is still there after the second save"
    assert all(r[1] == world.expert and r[3] is not None for r in rows)
    assert b"First wording" not in bytes(rows[0][2]), "sealed under the case key"
    kept = await CRYPTO.decrypt(aconn, world.case, bytes(rows[0][2]), purpose=SECTION_PURPOSE)
    assert kept == b"First wording here."
    audits = admin.execute(
        "select target from app.audit_log where case_id = %s and action = 'report.section_saved'", (world.case,)
    ).fetchall()
    assert len(audits) == 2 and all("wording" not in a[0] for a in audits), "audited, with no text"


async def test_pv12_the_section_history_cannot_be_changed_or_deleted(
    client_for: ClientFor, admin: psycopg.Connection, api_dsn: str, world: World
) -> None:
    key = writable(world.combo)
    saved = await (await client_for(world.expert)).put(
        world.url(f"report/sections/{key}"), json={"body": "Some wording.", "citations": []}
    )
    assert saved.status_code == 200, saved.text
    with pytest.raises(psycopg.Error):
        admin.execute("update app.report_section_versions set version_no = 9")
    with pytest.raises(psycopg.Error):
        admin.execute("delete from app.report_section_versions")
    with psycopg.connect(api_dsn, autocommit=True) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from app.report_section_versions")


# --- PV-18 -------------------------------------------------------------------------------------------------


async def test_pv18_control_a_build_records_its_job_so_the_code_version_can_be_joined(
    client_for: ClientFor, worker_db: Database, admin: psycopg.Connection, world: World
) -> None:
    job = await build_now(client_for, worker_db, admin, world)
    row = admin.execute(
        "select j.id, j.kind from app.report_builds b join app.jobs j on j.id = b.job_id where b.case_id = %s",
        (world.case,),
    ).fetchone()
    assert row is not None and str(row[0]) == job and row[1] == "build_report"


# --- rows of a locked version ------------------------------------------------------------------------------


async def test_rows_of_a_locked_version_cannot_be_changed_or_deleted_but_the_purge_removes_them(
    client_for: ClientFor,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: CaseFileStorage,
    api_dsn: str,
    worker_dsn: str,
) -> None:
    await signed_ready(client_for, worker_db, seed, admin, aconn, storage, world, reads)
    resp = await (await client_for(world.expert)).post(world.url("report/sign"), json=SIGN)
    assert resp.status_code == 200, resp.text
    statements = [
        "update app.report_paragraphs set para_no = para_no + 100 where case_id = %s",
        "delete from app.report_paragraphs where case_id = %s",
        "update app.citations set cite_no = cite_no + 100 where case_id = %s",
        "delete from app.citations where case_id = %s",
        "update app.report_snapshots set sha256 = repeat('0', 64) where case_id = %s",
        "delete from app.report_snapshots where case_id = %s",
        "update app.report_builds set page_count = 99 where case_id = %s",
        "delete from app.report_builds where case_id = %s",
    ]
    # The owner login (restores, support repairs) is not refused: the change is made, then rolled back.
    for sql in statements:
        with admin.transaction(force_rollback=True):
            assert admin.execute(sql, (world.case,)).rowcount >= 1, sql

    def through_a_definer_function(dsn: str, sql: str) -> int | None:
        """Run the statement in a security-definer function called on a real login of the given app role."""
        case = f"'{world.case}'"
        admin.execute(
            "create function app.zz_probe() returns bigint language plpgsql security definer"
            f" set search_path = '' as $$ declare n bigint; begin {sql % case};"
            " get diagnostics n = row_count; return n; end $$"
        )
        try:
            with psycopg.connect(dsn, autocommit=False) as conn:
                row = conn.execute("select app.zz_probe()").fetchone()
                conn.rollback()
                return int(row[0]) if row else None
        finally:
            admin.execute("drop function app.zz_probe()")

    # Reached from a login of either app role, even through a definer function, every one is refused (AVLCK).
    for dsn in (api_dsn, worker_dsn):
        for sql in statements:
            with pytest.raises(psycopg.errors.Error) as refused:
                through_a_definer_function(dsn, sql)
            assert refused.value.sqlstate == "AVLCK", sql
    # The case purge (the owner's function, which writes the purge marker) still removes them.
    with admin.transaction(force_rollback=True):
        admin.execute("select app.purge_case_rows(%s)", (world.case,))
        for table in ("report_paragraphs", "citations", "report_snapshots", "report_builds"):
            row = admin.execute(f"select count(*) from app.{table} where case_id = %s", (world.case,)).fetchone()
            assert row is not None and row[0] == 0, table
