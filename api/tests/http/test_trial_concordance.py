"""Screen 23, trial page numbers (slice I brief, `match_trial_bundle`, F35).

The synthetic trial bundle (`court_bundle_v2`) is re-ordered and re-numbered among other pages, leaves out
three cited pages and has five re-scanned at lower quality. Every page the expert read is matched to its
trial page or listed as missing; re-scanned pages wait for the expert to confirm. The list exports to CSV
and PDF (step-up: it leaves Avodeck), and "tell the instructing party" lists missing page numbers only.
A re-issued bundle supersedes the old one with a new list.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest

from app.core.db import Database
from tests.fixtures.trial.fixtures import (  # noqa: F401
    aconn,
    app,
    model,
    reads,
    storage,
    upload_bundle,
    worker_db,
    world,
)
from tests.fixtures.trial.world import CITED, PAGE_NOS, REMOVED, RESCANNED, World, trial_bundle
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo

pytestmark = pytest.mark.anyio

LAPSED = timedelta(minutes=11)


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    chosen: Combo = request.param
    return chosen


async def concordance(client_for: ClientFor, w: World, **query: str) -> dict[str, Any]:
    resp = await (await client_for(w.expert)).get(w.url("concordance"), params=query)
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


async def test_every_page_is_matched_to_its_trial_number_or_listed_as_missing(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    pdf, where = trial_bundle()
    sent = await upload_bundle(await client_for(world.expert), worker_db, world, pdf, received_on="2026-12-01")
    body = await concordance(client_for, world)
    assert body["bundle"]["id"] == sent["court_bundle_id"] and body["bundle"]["status"] == "matched"
    assert body["bundle"]["received_on"] == "2026-12-01" and body["bundle"]["page_count"] == len(where) + 9
    rows = {r["page_no"]: r for r in body["rows"]}
    assert set(rows) == set(PAGE_NOS)
    for n in PAGE_NOS:
        row = rows[n]
        if n in REMOVED:
            assert row["status"] == "missing" and row["trial_page_no"] is None, n
        elif n in RESCANNED:
            assert row["status"] == "to_confirm" and row["trial_page_no"] == where[n] and not row["confirmed"], n
        else:
            assert row["status"] == "matched" and row["trial_page_no"] == where[n] and row["confirmed"], n
    assert all(rows[n]["paragraphs"] for n in CITED)
    assert rows[214]["paragraphs"] == [2] and rows[125]["paragraphs"] == [4]
    assert body["counts"] == {"matched": 23, "to_confirm": 5, "missing": 3}
    missing = await concordance(client_for, world, status="missing")
    assert [r["page_no"] for r in missing["rows"]] == REMOVED


async def test_the_expert_confirms_a_weaker_match_and_others_cannot(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    pdf, where = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    row = next(r for r in (await concordance(client_for, world))["rows"] if r["page_no"] == RESCANNED[0])
    url = world.url(f"concordance/{row['id']}")
    headers = {"If-Match": f'"{row["version"]}"'}
    viewer = await client_for(world.viewer)
    assert (await viewer.patch(url, json={"trial_page_no": where[RESCANNED[0]]}, headers=headers)).status_code == 403
    outsider = await client_for(world.outsider)
    assert (await outsider.get(world.url("concordance"))).status_code == 404
    client = await client_for(world.expert)
    assert (await client.patch(url, json={"trial_page_no": 99_999}, headers=headers)).status_code == 422
    assert (await client.patch(url, json={"trial_page_no": where[RESCANNED[0]]})).status_code == 428
    done = await client.patch(url, json={"trial_page_no": where[RESCANNED[0]]}, headers=headers)
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "matched" and done.json()["method"] == "expert" and done.json()["confirmed"]
    assert (await client.patch(url, json={"trial_page_no": None}, headers=headers)).status_code == 412
    assert (await concordance(client_for, world))["counts"] == {"matched": 24, "to_confirm": 4, "missing": 3}


async def test_the_list_exports_to_csv_and_pdf_with_step_up(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    pdf, where = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    lapsed = await (await client_for(world.expert, mfa_age=LAPSED)).get(
        world.url("concordance/export"), params={"format": "csv"}
    )
    assert lapsed.status_code == 403
    client = await client_for(world.expert)
    csv = await client.get(world.url("concordance/export"), params={"format": "csv"})
    assert csv.status_code == 200 and csv.headers["content-type"].startswith("text/csv")
    assert csv.headers["cache-control"] == "private, no-store"
    lines = csv.text.strip().splitlines()
    assert lines[0] == "page_no,trial_page_no,status,score"
    assert f"118,{where[118]},matched," in csv.text and "214,,missing," in csv.text
    assert len(lines) == len(PAGE_NOS) + 1
    out = await client.get(world.url("concordance/export"), params={"format": "pdf"})
    assert out.status_code == 200 and out.content.startswith(b"%PDF")


async def test_telling_the_instructing_party_lists_missing_page_numbers_only(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf, received_on="2026-12-01")
    resp = await (await client_for(world.expert)).post(world.url("concordance/tell-instructing-party"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["missing"] == [
        {"page_no": 125, "paragraphs": [4]},
        {"page_no": 126, "paragraphs": [4]},
        {"page_no": 214, "paragraphs": [2]},
    ]
    assert body["received_on"] == "2026-12-01"
    assert set(body) == {"missing", "received_on", "bundle_version"}


async def test_a_reissued_bundle_supersedes_the_old_one_with_a_new_list(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    client = await client_for(world.expert)
    first, _ = trial_bundle()
    old = await upload_bundle(client, worker_db, world, first)
    again = await client.post(world.url("court-bundles"), content=first, headers={"content-type": "application/pdf"})
    assert again.status_code == 409 and again.json()["error"]["code"] == "bundle_exists"
    second, where = trial_bundle(removed=[214], rescanned=[], extra=3)
    resp = await client.post(
        world.url(f"court-bundles/{old['court_bundle_id']}/supersede"),
        content=second,
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 202, resp.text
    from tests.fixtures.trial.fixtures import run_job

    await run_job(worker_db, world, "match_trial_bundle", resp.json()["job_id"])
    body = await concordance(client_for, world)
    assert body["bundle"]["id"] == resp.json()["court_bundle_id"] and body["bundle"]["version"] == 2
    assert body["counts"] == {"matched": 30, "to_confirm": 0, "missing": 1}
    rows = {r["page_no"]: r for r in body["rows"]}
    assert rows[125]["trial_page_no"] == where[125]
    assert uuid.UUID(old["court_bundle_id"]) != uuid.UUID(body["bundle"]["id"])


async def test_pages_to_confirm_show_on_today(client_for: ClientFor, world: World, worker_db: Database) -> None:
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    today = (await (await client_for(world.expert)).get("/v1/today")).json()
    [item] = [i for i in today["needs_you"] if i["kind"] == "trial_pages_to_confirm"]
    assert item["case_id"] == str(world.case) and item["count"] == 5 and item["screen"] == "S23"


async def test_rows_carry_the_source_type_and_document_date_and_the_bundle_says_what_it_replaces(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    client = await client_for(world.expert)
    first, _ = trial_bundle()
    old = await upload_bundle(client, worker_db, world, first, received_on="2026-11-01")
    before = await concordance(client_for, world)
    assert before["bundle"]["replaces_received_on"] is None
    rows = {r["page_no"]: r for r in before["rows"]}
    assert rows[118]["document_date"] == "2024-03-12" and "source_type" in rows[118]
    second, _ = trial_bundle(removed=[214], rescanned=[], extra=3)
    resp = await client.post(
        world.url(f"court-bundles/{old['court_bundle_id']}/supersede"),
        content=second,
        headers={"content-type": "application/pdf"},
        params={"received_on": "2026-12-01"},
    )
    assert resp.status_code == 202, resp.text
    from tests.fixtures.trial.fixtures import run_job

    await run_job(worker_db, world, "match_trial_bundle", resp.json()["job_id"])
    after = await concordance(client_for, world)
    assert after["bundle"]["replaces_received_on"] == "2026-11-01"


async def test_the_server_says_the_missing_count_and_which_rows_show_first(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    body = await concordance(client_for, world)
    assert body["missing_count"] == 3 == body["counts"]["missing"]
    assert [r["page_no"] for r in body["missing_rows"]] == REMOVED
    check = [r["page_no"] for r in body["rows"] if r["status"] == "to_confirm" and not r["confirmed"]]
    shown = [r["page_no"] for r in body["display_rows"]]
    assert set(check) <= set(shown) and shown == sorted(shown)
    assert len(shown) == len(check) + 4
    assert body["hidden"] == len(body["rows"]) - len(shown)
    everything = await concordance(client_for, world, limit="all")
    assert everything["hidden"] == 0 and len(everything["display_rows"]) == len(body["rows"])
