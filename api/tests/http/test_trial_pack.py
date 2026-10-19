"""Screen 24, the trial pack (slice I brief, `build_trial_pack`, T-24).

- The pack is a large-print PDF (nothing under 14 pt) of the sections the expert chose. The served report's
  page references use trial page numbers wherever the match is confirmed; a page left out of the trial
  bundle keeps its old number marked as not in it (page 214), and an unconfirmed match is marked to confirm.
- It is built from the served report only (409 before serving), hashed, and goes stale when the court
  bundle is re-issued. Downloading it is an export (step-up).
- "Check a new note" says which sentences come from a served document (T-24 returns references only).
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from pypdf import PdfReader

from app.core.db import Database
from app.core.packs import default_library
from app.features.trial.words import word
from tests.fixtures.trial.fixtures import (  # noqa: F401
    Scripted,
    aconn,
    app,
    changes,
    model,
    reads,
    run_job,
    storage,
    upload_bundle,
    worker_db,
    world,
)
from tests.fixtures.trial.world import PLAN, Reads, World, build, trial_bundle
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LAPSED = timedelta(minutes=11)
ALL = ["report", "chronology", "joint_statement", "written_answers", "legal_tests"]


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    chosen: Combo = request.param
    return chosen


def pdf_text(data: bytes) -> str:
    import io

    reader = PdfReader(io.BytesIO(data))
    return " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages)


def font_sizes(data: bytes) -> set[float]:
    import io

    sizes: set[float] = set()
    reader = PdfReader(io.BytesIO(data))

    def visit(op: bytes, args: list[Any], cm: Any, tm: Any) -> None:
        if op == b"Tf":
            sizes.add(float(args[1]) * abs(float(tm[0]) or 1.0) * abs(float(cm[0]) or 1.0))

    for page in reader.pages:
        page.extract_text(visitor_operand_before=visit)
    return sizes


async def settings(client_for: ClientFor, w: World, version: int = 0, **body: Any) -> Any:
    return await (await client_for(w.expert)).put(
        w.url("trial-pack/settings"), json=body, headers={"If-Match": f'"{version}"'}
    )


async def built(client_for: ClientFor, worker_db: Database, w: World) -> dict[str, Any]:
    resp = await (await client_for(w.expert)).post(w.url("trial-pack"))
    assert resp.status_code == 202, resp.text
    await run_job(worker_db, w, "build_trial_pack", resp.json()["job_id"])
    latest = await (await client_for(w.expert)).get(w.url("trial-pack/latest"))
    assert latest.status_code == 200, latest.text
    body: dict[str, Any] = latest.json()
    return body


async def download(client_for: ClientFor, w: World) -> bytes:
    resp = await (await client_for(w.expert)).get(w.url("trial-pack/latest/download"))
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.headers["cache-control"] == "private, no-store"
    return resp.content


async def test_the_pack_uses_trial_numbers_and_marks_pages_not_in_the_bundle(
    client_for: ClientFor, world: World, worker_db: Database, combo: Combo
) -> None:
    pack = default_library().combine(*combo.codes)
    term = pack.term("court_bundle")
    pdf, where = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    assert (await settings(client_for, world, sections=ALL)).status_code == 200
    latest = await built(client_for, worker_db, world)
    assert latest["pack"]["status"] == "ready" and len(latest["pack"]["sha256"]) == 64
    assert latest["pack"]["stale"] is False and latest["settings"]["sections"] == ALL
    text = pdf_text(await download(client_for, world))
    assert f"[{term} p.{where[118]}]" in text and f"[{term} p.{where[105]}]" in text
    assert f"[p.214, {word(pack.language, 'not_in_bundle', court_bundle=term)}]" in text
    assert f"[p.103, {word(pack.language, 'trial_page_to_confirm')}]" in text
    assert "[p.118]" not in text
    assert PLAN in text  # the chronology of verified facts
    headings = [line[3:] for line in pack.legal_tests().splitlines() if line.startswith("## ")]
    assert not headings or headings[0] in text  # the pack's legal tests, when it has them


async def test_nothing_in_the_pack_is_smaller_than_14_point(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    assert (await settings(client_for, world, sections=ALL)).status_code == 200
    await built(client_for, worker_db, world)
    sizes = font_sizes(await download(client_for, world))
    assert sizes and min(sizes) >= 14, sizes


async def test_downloading_the_pack_needs_step_up(client_for: ClientFor, world: World, worker_db: Database) -> None:
    await built(client_for, worker_db, world)
    lapsed = await (await client_for(world.expert, mfa_age=LAPSED)).get(world.url("trial-pack/latest/download"))
    assert lapsed.status_code == 403 and lapsed.json()["error"]["code"] == "step_up_required"
    outsider = await client_for(world.outsider)
    assert (await outsider.get(world.url("trial-pack/latest"))).status_code == 404


async def test_settings_keep_a_version_and_take_known_sections_only(client_for: ClientFor, world: World) -> None:
    first = await settings(client_for, world, sections=["report", "chronology"], include_guidance=True)
    assert first.status_code == 200, first.text
    assert first.json()["version"] == 1 and first.headers["etag"] == '"1"'
    assert (await settings(client_for, world, sections=["report"])).status_code == 412
    assert (await settings(client_for, world, 1, sections=["zz_unknown"])).status_code == 422
    other_hearing = await settings(client_for, world, 1, sections=["report"], hearing_id=str(uuid.uuid4()))
    assert other_hearing.status_code == 422
    viewer = await (await client_for(world.viewer)).put(
        world.url("trial-pack/settings"), json={"sections": ["report"]}, headers={"If-Match": '"1"'}
    )
    assert viewer.status_code == 403


async def test_old_packs_go_stale_when_the_court_bundle_is_reissued(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    client = await client_for(world.expert)
    pdf, _ = trial_bundle()
    first = await upload_bundle(client, worker_db, world, pdf)
    await built(client_for, worker_db, world)
    second, _ = trial_bundle(removed=[], rescanned=[])
    resp = await client.post(
        world.url(f"court-bundles/{first['court_bundle_id']}/supersede"),
        content=second,
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 202
    latest = (await client.get(world.url("trial-pack/latest"))).json()
    assert latest["pack"]["stale"] is True and latest["pack"]["stale_reason"] == "court_bundle_superseded"


@pytest.fixture
async def unserved(
    seed: Seed, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, combo: Combo, reads: Reads
) -> World:
    return await build(seed, admin, aconn, combo, reads, words=False)


async def test_a_pack_needs_a_served_report(client_for: ClientFor, unserved: World) -> None:
    resp = await (await client_for(unserved.expert)).post(unserved.url("trial-pack"))
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "not_served"


async def test_a_note_sentence_found_in_no_served_document_is_flagged(
    client_for: ClientFor, world: World, model: Callable[..., Scripted]
) -> None:
    scripted = model()
    note = "The plan was recorded and repeated. Zz the patient later moved abroad."
    resp = await (await client_for(world.expert)).post(world.url("trial-pack/check-new"), json={"text": note})
    assert resp.status_code == 200, resp.text
    sentences = resp.json()["sentences"]
    assert [s["sourced"] for s in sentences] == [True, False]
    assert sentences[0]["source"] == {"kind": "report", "para_no": 2} and sentences[1]["source"] is None
    [request] = scripted.seen("T-24")
    assert "s0\tThe plan was recorded and repeated." in request.text
    viewer = await (await client_for(world.viewer)).post(world.url("trial-pack/check-new"), json={"text": note})
    assert viewer.status_code == 403


async def test_the_preview_counts_pages_per_section_and_lists_a_readiness_sheet_per_question(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    pdf, where = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    resp = await (await client_for(world.expert)).get(world.url("trial-pack/preview"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    keys = [s["key"] for s in body["sections"]]
    assert "report" in keys
    assert all(s["pages"] >= 1 for s in body["sections"])
    assert body["total_pages"] == sum(s["pages"] for s in body["sections"])
    for sheet in body["sheets"]:
        assert sheet["number"] >= 1 and sheet["text"]


async def test_the_readiness_section_is_accepted_and_adds_a_page_per_question(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    model()
    from tests.http.test_trial_meetings import agenda, create

    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    assert (await settings(client_for, world, sections=[*ALL, "readiness"])).status_code == 200
    preview = (await (await client_for(world.expert)).get(world.url("trial-pack/preview"))).json()
    assert len(preview["sheets"]) == 3 and preview["sheets"][0]["pages"][0]["page_no"] == 118
    assert "readiness" in [s["key"] for s in preview["sections"]]


async def test_each_readiness_sheet_knows_its_page_in_the_printed_pack_for_the_footer(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted], admin: psycopg.Connection
) -> None:
    model()
    from tests.http.test_trial_meetings import agenda, create

    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    preview = (await (await client_for(world.expert)).get(world.url("trial-pack/preview"))).json()
    row = admin.execute("select label from app.cases where id = %s", (world.case,)).fetchone()
    assert row is not None and preview["case_label"] == row[0]
    pages = {s["key"]: s["pages"] for s in preview["sections"]}
    keys = [s["key"] for s in preview["sections"]]
    start = 1 + sum(pages[k] for k in keys[: keys.index("readiness")])
    numbers = [s["pack_page_no"] for s in preview["sheets"]]
    assert numbers[0] == start and numbers == sorted(numbers) and len(set(numbers)) == len(numbers)
    assert all(n <= preview["total_pages"] for n in numbers)
