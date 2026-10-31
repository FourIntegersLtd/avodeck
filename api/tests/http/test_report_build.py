"""Slice F, building the report (screen 16): `POST .../report/build` queues `build_report` (202); the job
assembles the expert's own Word template in the confirmed heading order, fills each section's content
control, renders every citation to a checked fact on a read page and stores a new draft version.

Acceptance (slice F brief): the template's styles are kept and section order follows the confirmed
heading map; every citation renders as a page (or paragraph) reference and maps to a `citations` row
whose fact is citable; a citation style change rebuilds and keeps older drafts; the methodology appendix
lists each AI task with model, region, prompt version and date plus pages read out of the total, and no
content. F16: a template that cannot be used falls back to the standard layout and says so.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Iterator

import psycopg
import pytest
from fastapi import FastAPI
from psycopg.types.json import Jsonb

from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import SupabaseStorage
from app.features.report.jobs import build_report
from app.features.report.services import ReportServices, configure
from tests.fixtures.report.world import (
    CRYPTO,
    MODEL,
    RESULT,
    TEMPLATE,
    Reads,
    World,
    app_for,
    body_paragraphs,
    build,
    cite,
    save_opinion,
    save_template,
    template_headings,
    template_mapping,
)
from tests.support.contracts import add_question
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

OPINION = "The plan was written down [[cite:1]]. The result was low [[cite:2]].\n\nLater notes [[cite:3]]."


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
def services(reads: Reads, storage: SupabaseStorage) -> Iterator[None]:
    configure(ReportServices(crypto=CRYPTO, storage=storage, reads=reads))
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
async def world(
    seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads, aconn: psycopg.AsyncConnection
) -> World:
    w = build(seed, admin, combo, reads)
    await save_opinion(
        aconn,
        w.case,
        w.question,
        OPINION,
        [
            cite(w.plan, w.pages[0], "review 3/52"),
            cite(w.result, w.pages[1], "Ferritin 9"),
            cite(w.unread, w.pages[2], "seen"),
        ],
    )
    return w


async def build_now(client_for: ClientFor, worker_db: Database, w: World, person: uuid.UUID | None = None) -> None:
    queued = await (await client_for(person or w.expert)).post(w.url("report/build"))
    assert queued.status_code == 202, queued.text
    job_id = uuid.UUID(queued.json()["job_id"])
    await build_report(JobContext(worker_db, job_id, w.case, "build_report", 1))


async def latest_docx(
    admin: psycopg.Connection, storage: SupabaseStorage, aconn: psycopg.AsyncConnection, w: World
) -> bytes:
    row = admin.execute(
        "select docx_path from app.report_versions where case_id = %s order by version_no desc limit 1", (w.case,)
    ).fetchone()
    assert row is not None and row[0]
    return await CRYPTO.decrypt(aconn, w.case, await storage.get(row[0]), purpose=row[0])


@pytest.mark.usefixtures("services")
async def test_build_keeps_the_template_styles_and_follows_the_confirmed_heading_order(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    mapping, order = template_mapping(world.combo)
    await save_template(storage, admin, world.expert, mapping)
    await build_now(client_for, worker_db, world)

    rows = admin.execute(
        "select version_no, type, status, display_name, docx_path, pdf_path from app.report_versions"
        " where case_id = %s",
        (world.case,),
    ).fetchall()
    assert len(rows) == 1
    version_no, kind, status, name, docx_path, pdf_path = rows[0]
    assert (version_no, kind, status) == (1, "main", "draft")
    assert name and docx_path and pdf_path
    assert (await storage.get(docx_path))[:2] != b"PK", "files are stored sealed under the case key"

    paragraphs = body_paragraphs(await latest_docx(admin, storage, aconn, world))
    _, template_styles = template_headings(TEMPLATE.read_bytes())
    assert {style for style, _, _ in paragraphs} <= set(template_styles), "only the expert's own styles"
    sections = [tag.split(":", 1)[1] for _, _, tag in paragraphs if tag.startswith("section:")]
    seen = list(dict.fromkeys(sections))
    assert seen[: len(order)] == order, "sections follow the template's heading order"
    headings = [text for style, text, tag in paragraphs if style == "AE Heading" and not tag]
    template_texts = [h["text"] for h in template_headings(TEMPLATE.read_bytes())[0] if h["style"] == "AE Heading"]
    assert headings[: len(order)] == template_texts[: len(order)], "the expert's own heading words, unchanged"

    report = await (await client_for(world.expert)).get(world.url("report"))
    assert report.status_code == 200, report.text
    body = report.json()
    assert body["version"]["version_no"] == 1 and body["version"]["status"] == "draft"
    assert body["version"]["template_fallback"] is False
    assert body["version"]["page_count"] >= 1
    assert [s["key"] for s in body["sections"]][: len(order)] == order


@pytest.mark.usefixtures("services")
async def test_every_citation_renders_and_maps_to_a_row_whose_fact_is_citable(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    await build_now(client_for, worker_db, world)
    rows = admin.execute(
        "select c.cite_no, c.fact_id, c.page_id, c.fact_version from app.citations c"
        " join app.report_versions v on v.id = c.report_version_id where v.case_id = %s order by c.cite_no",
        (world.case,),
    ).fetchall()
    assert [(r[1], r[2]) for r in rows] == [(world.plan, world.pages[0]), (world.result, world.pages[1])]
    assert all(r[3] >= 1 for r in rows)
    text = " ".join(t for _, t, _ in body_paragraphs(await latest_docx(admin, storage, aconn, world)))
    assert re.search(r"\[p\.1\]", text) and re.search(r"\[p\.2\]", text), "page references by bundle page number"
    assert "[p.3]" not in text, "a fact on an unread page is never rendered as a reference"


@pytest.mark.usefixtures("services")
async def test_changing_the_citation_style_rebuilds_as_a_new_draft_and_keeps_the_older_one(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    await build_now(client_for, worker_db, world)
    client = await client_for(world.expert)
    current = await client.get(world.url("report/settings"))
    assert current.status_code == 200 and current.json()["citation_style"] == "page"
    changed = await client.patch(
        world.url("report/settings"),
        json={"citation_style": "paragraph"},
        headers={"If-Match": current.headers["etag"]},
    )
    assert changed.status_code == 202, changed.text
    job_id = uuid.UUID(changed.json()["job_id"])
    await build_report(JobContext(worker_db, job_id, world.case, "build_report", 1))

    versions = admin.execute(
        "select version_no, status, docx_path from app.report_versions where case_id = %s order by version_no",
        (world.case,),
    ).fetchall()
    assert [(v[0], v[1]) for v in versions] == [(1, "draft"), (2, "draft")]
    assert await storage.get(versions[0][2]), "the older draft's file is kept"
    text = " ".join(t for _, t, _ in body_paragraphs(await latest_docx(admin, storage, aconn, world)))
    assert "[1]" in text and "[2]" in text and "[p.1]" not in text
    listed = (await client.get(world.url("report"))).json()
    assert [v["version_no"] for v in listed["versions"]] == [2, 1]

    stale = await client.patch(
        world.url("report/settings"), json={"citation_style": "page"}, headers={"If-Match": '"1"'}
    )
    assert stale.status_code == 412


@pytest.mark.usefixtures("services")
async def test_the_methodology_appendix_lists_ai_tasks_and_pages_read_and_no_content(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    method = await (await client_for(world.expert)).get(world.url("report/methodology"))
    assert method.status_code == 200, method.text
    body = method.json()
    assert body["pages_read"] == 2 and body["pages_total"] == 3
    [task] = body["tasks"]
    assert (task["task_id"], task["model_id"], task["region"], task["prompt_version"]) == (
        "T-07b", MODEL, "zz", "2026-10-16.1",
    )  # fmt: skip
    assert task["calls"] == 1 and task["first_at"] and task["last_at"]

    await build_now(client_for, worker_db, world)
    paragraphs = body_paragraphs(await latest_docx(admin, storage, aconn, world))
    appendix = " ".join(t for _, t, tag in paragraphs if tag == "section:methodology")
    for value in ("T-07b", MODEL, "zz", "2026-10-16.1", "2 / 3"):
        assert value in appendix
    assert "review 3/52" not in appendix and RESULT not in appendix, "no record or opinion text"


@pytest.mark.usefixtures("services")
async def test_a_template_that_cannot_be_used_falls_back_to_the_standard_layout(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    mapping, _ = template_mapping(world.combo)
    await save_template(storage, admin, world.expert, mapping, data=b"not a word document")
    await build_now(client_for, worker_db, world)
    body = (await (await client_for(world.expert)).get(world.url("report"))).json()
    assert body["version"]["template_fallback"] is True
    styles = {style for style, _, _ in body_paragraphs(await latest_docx(admin, storage, aconn, world))}
    assert "AE Heading" not in styles


def require_template(admin: psycopg.Connection, org: uuid.UUID, template: uuid.UUID | None) -> None:
    policy = {} if template is None else {"required_template_id": str(template)}
    admin.execute("update app.organisations set policy = %s where id = %s", (Jsonb(policy), org))


@pytest.mark.usefixtures("services")
async def test_a_team_case_builds_in_the_organisations_required_template(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    mapping, order = template_mapping(world.combo)
    await save_template(storage, admin, world.expert, mapping, data=b"the expert's own is not a word document")
    required = await save_template(storage, admin, world.org, mapping, owner_type="organisation")
    require_template(admin, world.org, required)
    await build_now(client_for, worker_db, world)
    version = (await (await client_for(world.expert)).get(world.url("report"))).json()["version"]
    assert version["template_fallback"] is False, "the organisation's template was used, not the expert's"
    sections = [
        t.split(":", 1)[1]
        for _, _, t in body_paragraphs(await latest_docx(admin, storage, aconn, world))
        if t.startswith("section:")
    ]
    assert list(dict.fromkeys(sections))[: len(order)] == order
    used = admin.execute(
        "select template_id from app.report_builds where case_id = %s order by created_at desc limit 1", (world.case,)
    ).fetchone()
    assert used == (required,)


@pytest.mark.usefixtures("services")
async def test_without_a_required_template_the_experts_own_is_used(
    client_for: ClientFor,
    worker_db: Database,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    world: World,
) -> None:
    mapping, _ = template_mapping(world.combo)
    own = await save_template(storage, admin, world.expert, mapping)
    await save_template(storage, admin, world.org, mapping, data=b"not a word document", owner_type="organisation")
    require_template(admin, world.org, None)
    await build_now(client_for, worker_db, world)
    version = (await (await client_for(world.expert)).get(world.url("report"))).json()["version"]
    assert version["template_fallback"] is False
    used = admin.execute(
        "select template_id from app.report_builds where case_id = %s order by created_at desc limit 1", (world.case,)
    ).fetchone()
    assert used == (own,)


@pytest.mark.usefixtures("services")
async def test_preview_pages_are_images_that_are_never_cached(
    client_for: ClientFor, worker_db: Database, world: World
) -> None:
    client = await client_for(world.expert)
    assert (await client.get(world.url("report/preview/1"))).status_code == 404, "nothing built yet"
    await build_now(client_for, worker_db, world)
    page = await client.get(world.url("report/preview/1"))
    assert page.status_code == 200
    assert page.headers["content-type"] == "image/png"
    assert page.headers["cache-control"] == "private, no-store"
    assert page.content[:8] == b"\x89PNG\r\n\x1a\n"
    assert (await client.get(world.url("report/preview/999"))).status_code == 404


async def test_who_may_build_and_read_the_report(client_for: ClientFor, world: World) -> None:
    outsider = await client_for(world.outsider)
    assert (await outsider.post(world.url("report/build"))).status_code == 404
    assert (await outsider.get(world.url("report"))).status_code == 404
    assert (await outsider.get(world.url("report/methodology"))).status_code == 404
    assert (await (await client_for(world.viewer)).post(world.url("report/build"))).status_code == 403
    assert (await (await client_for(world.assistant)).post(world.url("report/build"))).status_code == 202
    viewer = await (await client_for(world.viewer)).get(world.url("report"))
    assert viewer.status_code == 200 and viewer.json()["version"] is None


async def test_importing_from_word_is_not_built_yet(client_for: ClientFor, world: World) -> None:
    answer = await (await client_for(world.expert)).post(world.url("report/word-import"))
    assert answer.status_code == 501


async def test_the_opinion_section_counts_answered_questions(
    client_for: ClientFor, admin: psycopg.Connection, world: World
) -> None:
    """Screen 16 shows '1 of 2' beside the opinion section: questions with an opinion out of confirmed questions."""
    add_question(admin, world.case, status="confirmed")
    body = (await (await client_for(world.expert)).get(world.url("report"))).json()
    by_key = {s["key"]: s for s in body["sections"]}
    assert (by_key["opinion"]["done"], by_key["opinion"]["total"]) == (1, 2)
    others = [s for k, s in by_key.items() if k != "opinion"]
    assert others and all(s["done"] is None and s["total"] is None for s in others)


@pytest.mark.usefixtures("services")
async def test_paragraphs_are_the_built_reports_words_as_text(
    client_for: ClientFor, worker_db: Database, world: World
) -> None:
    """Screen 16's text preview: read-only, one section at a time, nothing before a build, same people as the report."""
    client = await client_for(world.expert)
    assert (await client.get(world.url("report/paragraphs"))).status_code == 404, "nothing built yet"
    await build_now(client_for, worker_db, world)
    everything = (await client.get(world.url("report/paragraphs"))).json()
    assert [p["para_no"] for p in everything["paragraphs"]] == sorted(p["para_no"] for p in everything["paragraphs"])
    mine = (await client.get(world.url("report/paragraphs"), params={"section": "opinion"})).json()["paragraphs"]
    assert mine and {p["section"] for p in mine} == {"opinion"}
    assert any("Ferritin 9" in p["text"] or "plan was written down" in p["text"] for p in mine)
    assert (await client.get(world.url("report/paragraphs"), params={"section": "Bad Key"})).status_code == 422
    assert (await (await client_for(world.outsider)).get(world.url("report/paragraphs"))).status_code == 404
    assert (await (await client_for(world.viewer)).get(world.url("report/paragraphs"))).status_code == 200


@pytest.mark.usefixtures("services")
async def test_the_report_names_its_document_and_the_court_track_from_the_pack_and_case(
    client_for: ClientFor, worker_db: Database, admin: psycopg.Connection, world: World
) -> None:
    await build_now(client_for, worker_db, world)
    client = await client_for(world.expert)
    plain = (await client.get(world.url("report"))).json()
    assert plain["court_track_label"] is None, "a case with no track names none"

    admin.execute("update app.cases set court_track = 'multi' where id = %s", (world.case,))
    body = (await client.get(world.url("report"))).json()
    sections = world.combo.raw("case-type", "report_sections.yaml") or {}
    vocabulary = world.combo.raw("jurisdiction", "vocabulary.yaml") or {}
    name = sections.get("report_name")
    version = body["version"]
    assert version["title"] == (f"{name}: Case Z" if name else version["display_name"])
    assert body["versions"][0]["title"] == version["title"]
    tracks = vocabulary.get("court_tracks") or {}
    assert body["court_track_label"] == tracks.get("multi", "multi")


@pytest.mark.usefixtures("services")
async def test_the_report_says_whether_it_is_locked_and_counts_its_checklist(
    client_for: ClientFor, worker_db: Database, admin: psycopg.Connection, world: World
) -> None:
    await build_now(client_for, worker_db, world)
    client = await client_for(world.expert)
    body = (await client.get(world.url("report"))).json()
    assert body["locked"] is False
    items = body["checklist"]["items"]
    assert body["checklist"]["total"] == len(items)
    assert body["checklist"]["done"] == sum(1 for i in items if i["done"])

    for status in ("locked", "served"):
        admin.execute(
            "update app.report_versions set status = %s, sha256 = coalesce(sha256, repeat('a', 64)) where case_id = %s",
            (status, world.case),
        )
        assert (await client.get(world.url("report"))).json()["locked"] is (status == "served")
    assert (await client.get(world.url("report"))).json()["locked"] is True
    admin.execute(
        "update app.report_versions set status = 'withdrawn', withdrawn_reason = 'factual_error',"
        " withdrawn_at = now() where case_id = %s",
        (world.case,),
    )
    assert (await client.get(world.url("report"))).json()["locked"] is True
