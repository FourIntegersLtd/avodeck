"""Slice A: pages the model could not read are tried again, and only those (`retry_unread_pages`).

- `record_entries.retryable_unread_pages`: extracted pages whose answer failed, not set aside, not copies.
- `record_entries.request_unread_retry`: queues the job once; nothing while one waits, while the pipeline
  runs, or when no page is unread. The same count shows as `failed_pages` on the bundle summary.
- The job clears only those pages' failed marker and extracts them again the way they were first extracted;
  pages that read well and every verified fact are left alone. A page that fails again is left for a person.
- `POST /transcript/retry` and `GET /bundle/summary` give the browser the status and the count.
Messages name counts only (rule 6).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.contracts import record_entries
from app.core.db import Database
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_extract import _open
from tests.repository.test_pipeline_record_entries import _analysed, answer_with, two_entries
from tests.repository.test_pipeline_support import t07b_requests, text_pdf
from tests.support.ai import ScriptedBedrock, response
from tests.support.db import scope_for
from tests.support.facts_seed import insert_fact
from tests.support.files import local_storage
from tests.support.packs import BOTH, LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

RETRY = "retry_unread_pages"


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


class Queue:
    def __init__(self) -> None:
        self.queued: list[tuple[Any, ...]] = []

    async def enqueue(self, *args: Any, **kw: Any) -> uuid.UUID:
        self.queued.append(args)
        return uuid.uuid4()


def case_of(seed: Seed, *, combo: Any = LAUNCH) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    return expert, case, seed.document(case)


async def test_only_pages_whose_answer_failed_are_retryable(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    good, failed, held, copy, never = (seed.page(case, doc, n) for n in (1, 2, 3, 4, 5))
    _analysed(admin, case, good, read=True)
    _analysed(admin, case, failed, read=False, needs_human=True, failed=True, reason="invalid_output")
    _analysed(admin, case, held, read=False, needs_human=True, failed=True, reason="invalid_output")
    _analysed(admin, case, copy, read=False, needs_human=True, failed=True, reason="invalid_output")
    _analysed(admin, case, never, read=False)
    admin.execute("update app.pages set quarantined = true where id = %s", (held,))
    admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (good, copy))
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.retryable_unread_pages(conn, case) == 1


async def test_a_retry_is_queued_once_and_only_when_a_page_is_unread(
    monkeypatch: pytest.MonkeyPatch, api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    good, failed = seed.page(case, doc, 1), seed.page(case, doc, 2)
    _analysed(admin, case, good, read=True)
    queue = Queue()
    monkeypatch.setattr(record_entries, "registry", queue)
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.request_unread_retry(conn, case) is None, "nothing unread, nothing queued"
    _analysed(admin, case, failed, read=False, needs_human=True, failed=True, reason="invalid_output")
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.request_unread_retry(conn, case) is not None
    assert [args[2] for args in queue.queued] == [RETRY]
    admin.execute("insert into app.jobs (case_id, kind) values (%s, %s)", (case, RETRY))
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.entries_status(conn, case) == "preparing", "the retry counts as preparing"
        assert await record_entries.request_unread_retry(conn, case) is None, "one is waiting"
    assert len(queue.queued) == 1


async def test_no_retry_is_queued_while_the_pipeline_runs(
    monkeypatch: pytest.MonkeyPatch, api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    expert, case, doc = case_of(seed)
    _analysed(admin, case, seed.page(case, doc, 1), read=False, needs_human=True, failed=True, reason="invalid_output")
    admin.execute("insert into app.jobs (case_id, kind) values (%s, 'process_bundle')", (case,))
    queue = Queue()
    monkeypatch.setattr(record_entries, "registry", queue)
    async with api_db.request(scope_for(expert)) as conn:
        assert await record_entries.request_unread_retry(conn, case) is None
    assert queue.queued == []


async def test_the_job_asks_only_about_the_unread_pages_and_leaves_the_rest(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    pack = default_library().combine(*LAUNCH.codes)
    document = text_pdf([["Seen on 12/03/2024", "by the nurse", "blood pressure fine"]])
    words = embedded_words(split_pages(document)[0]).words
    bedrock = ScriptedBedrock([response({"unexpected": "shape"})])  # never valid
    async with await _open(
        LAUNCH,
        FixtureStore(tmp_path / "nothing-recorded"),
        bedrock,
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    ) as h:
        await h.upload(document)
        assert set((await h.drain()).values()) == {"done"}
        (page,) = h.pages()
        # A page that read well, with a verified fact; and a suggestion on the failed page.
        doc = seed.document(h.case_id)
        good = seed.page(h.case_id, doc, 2)
        _analysed(admin, h.case_id, good, read=True)
        verified = insert_fact(admin, h.case_id, good, "note", value="x", status="verified")
        suggested = insert_fact(admin, h.case_id, page["id"], "note", value="y", status="suggested")
        good_before = admin.execute("select extracted_at from app.page_analysis where page_id = %s", (good,)).fetchone()
        assert admin.execute(
            "select entries_failed_at is not null from app.page_analysis where page_id = %s", (page["id"],)
        ).fetchone() == (True,)

        bedrock.script = [answer_with(pack, "", words, two_entries(pack, words))]  # the model answers now
        asked = len(t07b_requests(h))
        monkeypatch.setattr(record_entries, "registry", h.registry)  # the harness runs its own queue
        async with api_db.request(scope_for(h.expert)) as conn:
            await record_entries.request_unread_retry(conn, h.case_id)
        assert set((await h.drain()).values()) == {"done"}

        assert len(t07b_requests(h)) - asked == 1, "one question, for the one unread page"
        row = admin.execute(
            "select entries_extracted_at is not null, entries_failed_at is null, needs_human, reason"
            " from app.page_analysis where page_id = %s",
            (page["id"],),
        ).fetchone()
        assert row == (True, True, False, None)
        assert admin.execute(
            "select count(*) from app.record_entries where page_id = %s", (page["id"],)
        ).fetchone() == (2,)
        assert admin.execute("select extracted_at from app.page_analysis where page_id = %s", (good,)).fetchone() == (
            good_before
        )
        assert admin.execute("select status from app.facts where id = %s", (verified,)).fetchone() == ("verified",)
        assert admin.execute("select status from app.facts where id = %s", (suggested,)).fetchone() == ("rejected",)
        async with api_db.request(scope_for(h.expert)) as conn:
            assert await record_entries.entries_unread_pages(conn, h.case_id) == []


async def test_a_page_that_fails_again_is_left_for_a_person_and_nothing_asks_again_on_its_own(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    document = text_pdf([["Seen on 12/03/2024", "by the nurse", "blood pressure fine"]])
    bedrock = ScriptedBedrock([response({"unexpected": "shape"})])
    async with await _open(
        LAUNCH,
        FixtureStore(tmp_path / "nothing-recorded"),
        bedrock,
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    ) as h:
        await h.upload(document)
        await h.drain()
        (page,) = h.pages()
        monkeypatch.setattr(record_entries, "registry", h.registry)
        async with api_db.request(scope_for(h.expert)) as conn:
            await record_entries.request_unread_retry(conn, h.case_id)
        assert set((await h.drain()).values()) == {"done"}
        async with api_db.request(scope_for(h.expert)) as conn:
            assert await record_entries.entries_unread_pages(conn, h.case_id) == [page["bundle_page_no"]]
            assert await record_entries.entries_status(conn, h.case_id) == "ready"
        asked = len(t07b_requests(h))
        await h.drain()
        assert len(t07b_requests(h)) == asked, "nothing asks again on its own"


@pytest.fixture(params=BOTH, ids=lambda c: c.codes[0])
async def world(
    request: pytest.FixtureRequest, admin: psycopg.Connection, api_db: Database, seed: Seed
) -> dict[str, Any]:
    expert, case, doc = case_of(seed, combo=request.param)
    for n in (1, 2, 3):
        _analysed(
            admin,
            case,
            seed.page(case, doc, n),
            read=n == 1,
            needs_human=n != 1,
            failed=n != 1,
            reason=None if n == 1 else "invalid_output",
        )
    return {"expert": expert, "case": case}


async def test_the_summary_counts_the_pages_not_processed_and_whether_a_retry_is_under_way(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any
) -> None:
    client = await client_for(world["expert"])
    body = (await client.get(f"/v1/cases/{world['case']}/bundle/summary")).json()
    assert (body["failed_pages"], body["retrying"]) == (2, False)
    admin.execute("insert into app.jobs (case_id, kind) values (%s, %s)", (world["case"], RETRY))
    body = (await client.get(f"/v1/cases/{world['case']}/bundle/summary")).json()
    assert (body["failed_pages"], body["retrying"]) == (2, True)


async def test_the_transcript_retry_route_queues_the_page_retry_once(
    world: dict[str, Any], admin: psycopg.Connection, client_for: Any
) -> None:
    client = await client_for(world["expert"])
    for _ in range(2):
        resp = await client.post(f"/v1/cases/{world['case']}/transcript/retry")
        assert resp.status_code == 202 and resp.json()["status"] == "preparing"
    row = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = %s", (world["case"], RETRY)
    ).fetchone()
    assert row == (1,)
