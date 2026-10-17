"""Slice A increment 11a: starting, stopping and reprocessing (architecture 5.4, 10.3; brief behaviour rules).

- `POST /v1/cases/{id}/process` (case.upload) queues the bundle job: 202 + job.
- `POST /v1/cases/{id}/process/cancel` (case.upload) stops processing at a page boundary: pages being
  read finish, no new page starts, the job ends `paused` with reason `cancelled`, and finished pages
  are kept. Processing again later only does the pages that were not finished.
- `POST /v1/cases/{id}/reprocess` (case.upload) is the opt-in to read every page again with the
  cell's current model: verified facts never change, earlier unverified suggestions on a page are
  superseded, and new suggestions carry their model ID. Nothing is reprocessed without it.
Viewers get 403 and people outside the case 404. Runs under the launch and the fixture packs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import httpx
import psycopg
import pytest

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.cell import model_for
from app.core.db import Database
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import SupabaseStorage
from tests.repository.test_pipeline_support import Harness, facts_of, harness, t07b_requests, text_pdf
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PAGES = [[f"Synthetic {word} entry"] for word in ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf")]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


def answer(pack: Any, *, value: str = "Synthetic") -> ModelResponse:
    """A valid T-07b answer with one fact citing the page's first word."""
    first = embedded_words(split_pages(text_pdf(PAGES[:1]))[0]).words[0]
    kind = pack.fact_kinds()[0]["key"]
    return response(
        {
            "source_type": pack.source_types()[0]["key"],
            "printed_dates": [],
            "facts": [{"kind": kind, "clinician_role": None, "fields": {}, "value": value, "word_ids": [first.id]}],
            "references_out": [],
            "availability_evidence": [],
            "actions_requested_of_clinician": [],
        }
    )


class CancellingBedrock(ScriptedBedrock):
    """Answers like `ScriptedBedrock`, and asks to cancel processing while the first page is being read."""

    def __init__(self, script: list[ModelResponse | Exception]) -> None:
        super().__init__(script)
        self.client: httpx.AsyncClient | None = None
        self.path = ""
        self.cancel_status: int | None = None

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        if self.client is not None and self.cancel_status is None:
            self.cancel_status = (await self.client.post(self.path)).status_code
        return await super().invoke(request)


@pytest.fixture(params=BOTH, ids=lambda c: c.codes[0])
async def h(
    request: pytest.FixtureRequest,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> AsyncIterator[Harness]:
    combo: Combo = request.param
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    pack = default_library().combine(*combo.codes)
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=CancellingBedrock([answer(pack)]),
    ) as running:
        running.pack = pack  # type: ignore[attr-defined]
        yield running


def extracted(h: Harness) -> set[uuid.UUID]:
    return {
        r[0]
        for r in h.admin.execute(
            "select page_id from app.page_analysis where case_id = %s and extracted_at is not null", (h.case_id,)
        )
    }


def bundle_jobs(h: Harness) -> list[tuple[str, str | None]]:
    return h.admin.execute(
        "select status, error_code from app.jobs where case_id = %s and kind = 'process_bundle' order by created_at",
        (h.case_id,),
    ).fetchall()


async def test_processing_starts_on_request(h: Harness) -> None:
    sent = await h.client.post(f"/v1/cases/{h.case_id}/process")
    assert sent.status_code == 202, sent.status_code
    job = uuid.UUID(sent.json()["job_id"])
    assert h.admin.execute("select kind from app.jobs where id = %s", (job,)).fetchone() == ("process_bundle",)
    assert set((await h.drain()).values()) == {"done"}


async def test_cancelling_stops_at_a_page_boundary_and_keeps_finished_pages(h: Harness) -> None:
    bedrock: CancellingBedrock = h.bedrock
    bedrock.client = h.client
    bedrock.path = f"/v1/cases/{h.case_id}/process/cancel"
    await h.upload(text_pdf(PAGES))
    await h.drain()
    assert bedrock.cancel_status == 202
    assert bundle_jobs(h)[-1] == ("paused", "cancelled")
    asked = len(t07b_requests(h))
    done = extracted(h)
    assert 1 <= asked < len(PAGES), "cancelling did not stop new pages starting"
    assert len(done) == asked, "a page being read when processing was cancelled was not finished"
    assert {f["page_id"] for f in facts_of(h)} == done
    assert h.admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'bundle.processing_cancelled'",
        (h.case_id,),
    ).fetchone() == (1,)

    bedrock.client = None
    again = await h.client.post(f"/v1/cases/{h.case_id}/process")
    assert again.status_code == 202
    assert set((await h.drain()).values()) >= {"done"}
    assert bundle_jobs(h)[-1] == ("done", None)
    assert len(extracted(h)) == len(PAGES)
    assert len(t07b_requests(h)) == len(PAGES), "a finished page was read again"


async def test_reprocessing_is_opt_in_and_never_changes_verified_facts(h: Harness) -> None:
    await h.upload(text_pdf(PAGES[:3]))
    await h.drain()
    first = facts_of(h)
    assert len(first) == 3
    kept = first[0]
    h.admin.execute("update app.facts set status = 'verified' where id = %s", (kept["id"],))
    (kept_version,) = h.admin.execute("select version from app.facts where id = %s", (kept["id"],)).fetchone()  # type: ignore[misc]

    assert (await h.client.post(f"/v1/cases/{h.case_id}/process")).status_code == 202
    await h.drain()
    assert len(t07b_requests(h)) == 3, "pages were read again without the expert opting in"

    sent = await h.client.post(f"/v1/cases/{h.case_id}/reprocess")
    assert sent.status_code == 202, sent.status_code
    job = uuid.UUID(sent.json()["job_id"])
    assert h.admin.execute("select kind from app.jobs where id = %s", (job,)).fetchone() == ("reprocess_case",)
    assert set((await h.drain()).values()) == {"done"}
    assert len(t07b_requests(h)) == 6

    after = {f["id"]: f for f in facts_of(h)}
    assert after[kept["id"]]["status"] == "verified"
    assert after[kept["id"]]["version"] == kept_version
    assert after[kept["id"]]["value"] == kept["value"]
    for old in first[1:]:
        assert after[old["id"]]["status"] == "rejected", "an earlier unverified suggestion was not superseded"
    new = [f for f in after.values() if f["id"] not in {o["id"] for o in first}]
    assert len(new) == 3 and {f["status"] for f in new} == {"suggested"}
    model = model_for(cell(), "sonnet")
    assert {f["model_id"] for f in new} == {model}
    assert h.admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'bundle.reprocess_requested'",
        (h.case_id,),
    ).fetchone() == (1,)

    await h.enqueue("reprocess_case")
    await h.drain()
    assert len(t07b_requests(h)) == 6, "a page already read again was read a third time"


async def test_only_people_who_upload_may_start_stop_or_reprocess(
    h: Harness, seed: Seed, client_for: ClientFor
) -> None:
    viewer, assistant = seed.person(), seed.person()
    seed.case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    seed.case_member(h.case_id, assistant, "assistant", added_by=h.expert)
    stranger = await client_for(seed.person())
    refused = await client_for(viewer)
    helper = await client_for(assistant)
    for path in ("process", "process/cancel", "reprocess"):
        url = f"/v1/cases/{h.case_id}/{path}"
        assert (await stranger.post(url)).status_code == 404, path
        assert (await refused.post(url)).status_code == 403, path
        assert (await helper.post(url)).status_code == 202, path


async def test_processing_controls_are_kept_per_case_for_people_who_upload(
    admin: psycopg.Connection, seed: Seed, api_db: Database
) -> None:
    from tests.support.db import scope_for

    expert, viewer, stranger = seed.person(), seed.person(), seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert)
    seed.case_member(case_id, viewer, "viewer", added_by=expert)
    upsert = (
        "insert into app.case_processing (case_id, cancel_requested_at, cancel_requested_by)"
        " values (%s, now(), %s) on conflict (case_id) do update set cancel_requested_at = now()"
    )
    async with api_db.request(scope_for(expert)) as conn:
        await conn.execute(upsert, (case_id, expert))
        await conn.execute(upsert, (case_id, expert))
    for person in (viewer, stranger):
        async with api_db.request(scope_for(person)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(upsert, (case_id, person))
    async with api_db.request(scope_for(viewer)) as conn:
        cur = await conn.execute("select count(*) from app.case_processing where case_id = %s", (case_id,))
        assert await cur.fetchone() == (1,)
    async with api_db.request(scope_for(stranger)) as conn:
        cur = await conn.execute("select count(*) from app.case_processing where case_id = %s", (case_id,))
        assert await cur.fetchone() == (0,)


async def test_the_preparing_screen_finds_the_case_s_latest_processing_job(
    h: Harness, seed: Seed, client_for: ClientFor
) -> None:
    """`GET /v1/cases/{id}/process` (case.view_metadata) names the latest processing job, or none,
    so screen 07 can follow it after an upload without being handed its id. Numbers and codes only."""
    url = f"/v1/cases/{h.case_id}/process"
    nothing = await h.client.get(url)
    assert nothing.status_code == 200, nothing.status_code
    assert nothing.json() == {"job": None}

    await h.upload(text_pdf(PAGES[:2]))
    queued = (await h.client.get(url)).json()["job"]
    (first,) = h.admin.execute(
        "select id from app.jobs where case_id = %s order by created_at desc limit 1", (h.case_id,)
    ).fetchone()  # type: ignore[misc]
    assert queued["id"] == str(first), "the upload's first job (the scan) is not followed"
    assert queued["status"] in ("queued", "running")
    assert set(queued) == {"id", "kind", "status", "step", "pct", "eta_seconds", "error_code"}

    await h.drain()
    (latest,) = h.admin.execute(
        "select id from app.jobs where case_id = %s and kind = 'process_bundle' order by created_at desc limit 1",
        (h.case_id,),
    ).fetchone()  # type: ignore[misc]
    done = (await h.client.get(url)).json()["job"]
    assert done["id"] == str(latest), "the bundle job queued after the scan is not the one named"
    assert (done["kind"], done["status"], done["pct"]) == ("process_bundle", "done", 100)

    viewer = seed.person()
    seed.case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    assert (await (await client_for(viewer)).get(url)).json()["job"]["id"] == str(latest)
    assert (await (await client_for(seed.person())).get(url)).status_code == 404


async def test_reprocessed_suggestions_carry_the_model_id_that_made_them(h: Harness) -> None:
    """Brief: reprocess suggestions carry their model ID (and the task and prompt version) by name."""
    from app.features.pipeline import extract

    await h.upload(text_pdf(PAGES[:2]))
    await h.drain()
    first = {f["id"] for f in facts_of(h)}
    assert (await h.client.post(f"/v1/cases/{h.case_id}/reprocess")).status_code == 202
    await h.drain()
    new = [f for f in facts_of(h) if f["id"] not in first]
    assert len(new) == 2
    task = extract.task_for(h.pack)  # type: ignore[attr-defined]
    model = model_for(cell(), task.model)
    for fact in new:
        assert fact["model_id"] == model
        assert fact["model_task"] == task.task_id
        assert fact["prompt_version"] == task.prompt_version
