"""Page-batch jobs end to end (ADR 0038, build step 10): `read_page_batch`, the parent's two phases and its tail.

The committed 18-page subset (recorded OCR and model answers) is cut into batches of 5 pages with a window of 2:
four OCR children, then four sort children, all on the queue with no case lock, the parent parked in between. With the
flag on the pages end up exactly as with the flag off. Messages carry counts, ids and codes only (rule 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import logging
import uuid
from collections import Counter
from collections.abc import AsyncIterator, Iterator, Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.config import PipelineBatchSettings
from app.core.db import Database
from app.core.jobs import RESUMABLE_PAUSES, JobContext
from app.core.storage import CaseFileStorage
from app.features.pipeline import repo, service
from tests.repository.test_pipeline_support import (
    Harness,
    expected,
    facts_of,
    harness,
    recorded_book,
    rekeyed_store,
    subset_document,
    subset_page_bytes,
)
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ON = PipelineBatchSettings(pages=5, window=2, min_pages=1)
PAGES = 18
BATCHES = 4  # ceil(18 / 5), in each of the two phases
RANK = {"split": 0, "ocr": 1, "sort": 2, "duplicates": 3, "missing": 4}


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def recordings(tmp_path: Path) -> FixtureStore:
    return rekeyed_store(tmp_path / "recordings", subset_document(), subset_page_bytes())


@contextlib.asynccontextmanager
async def opened(
    batches: PipelineBatchSettings | None,
    store: FixtureStore,
    *,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    case = expected("case.json")
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=store,
        bedrock=recorded_book(store),
        names=list(case["case_names"]),
        dates_of_birth=[dt.date.fromisoformat(case["dob"])],
        batches=batches,
    ) as h:
        yield h


async def process(h: Harness, *, sample: bool = False) -> list[tuple[str | None, int | None]]:
    """Upload the subset and run the worker until every job of the case has finished. With `sample`, the
    parent's (step, pct) is read every few milliseconds meanwhile and returned in the order it was seen."""
    await h.upload(subset_document())
    seen: list[tuple[str | None, int | None]] = []

    async def watch() -> None:
        while True:
            row = h.admin.execute(
                "select step, pct from app.jobs where case_id = %s and kind in ('process_bundle', 'process_pages')"
                " order by created_at desc limit 1",
                (h.case_id,),
            ).fetchone()
            if row is not None and (not seen or seen[-1] != (row[0], row[1])):
                seen.append((row[0], row[1]))
            await asyncio.sleep(0.005)

    watcher = asyncio.create_task(watch()) if sample else None
    try:
        finished = await h.drain(timeout=300)
    finally:
        if watcher is not None:
            watcher.cancel()
    assert set(finished.values()) == {"done"}, finished
    return seen


def parent_of(h: Harness) -> uuid.UUID:
    row = h.admin.execute(
        "select id from app.jobs where case_id = %s and kind in ('process_bundle', 'process_pages')"
        " order by created_at limit 1",
        (h.case_id,),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def children_of(h: Harness) -> list[uuid.UUID]:
    return [
        r[0]
        for r in h.admin.execute(
            "select id from app.jobs where case_id = %s and kind = 'read_page_batch' order by created_at", (h.case_id,)
        )
    ]


def outcome(h: Harness) -> dict[str, Any]:
    """What a processed case holds, with ids left out: words, facts, analysis, entries, embeddings per page number."""
    q = h.admin.execute
    case = (h.case_id,)
    return {
        "words": q(
            "select p.bundle_page_no, w.word_id, w.struck, w.n from app.ocr_words w"
            " join app.pages p on p.id = w.page_id"
            " where p.bundle_id = (select bundle_id from app.cases where id = %s) order by 1, 4",
            case,
        ).fetchall(),
        "pages": q(
            "select bundle_page_no, text_source, unreadable, language, quarantined from app.pages"
            " where bundle_id = (select bundle_id from app.cases where id = %s) order by 1",
            case,
        ).fetchall(),
        # Facts of one page are written in one transaction (equal `created_at`), so their order is not defined: compare
        # them as a set per page.
        "facts": sorted(
            (
                (f["bundle_page_no"], f["kind"], f["status"], f["event_date"], sorted(f["word_ids"] or []))
                for f in facts_of(h)
            ),
            key=repr,
        ),
        "analysis": q(
            "select p.bundle_page_no, a.reason, a.printed_dates from app.page_analysis a"
            " join app.pages p on p.id = a.page_id"
            " where a.bundle_id = (select bundle_id from app.cases where id = %s) order by 1",
            case,
        ).fetchall(),
        "entries": q(
            "select p.bundle_page_no, count(*) from app.record_entries e join app.pages p on p.id = e.page_id"
            " where p.bundle_id = (select bundle_id from app.cases where id = %s) group by 1 order by 1",
            case,
        ).fetchall(),
        "embeddings": q(
            "select p.bundle_page_no, count(*) from app.page_embeddings e join app.pages p on p.id = e.page_id"
            " where p.bundle_id = (select bundle_id from app.cases where id = %s) group by 1 order by 1",
            case,
        ).fetchall(),
        "duplicates": q(
            "select count(*) from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
            " and superseded_by_page_id is not null",
            case,
        ).fetchall(),
        "gaps": q("select count(*) from app.record_gaps where case_id = %s", case).fetchall(),
        "missing": q("select count(*) from app.missing_items where case_id = %s", case).fetchall(),
    }


async def test_flag_on_gives_the_same_results_as_flag_off(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    kw: dict[str, Any] = dict(
        admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, seed=seed, storage=storage
    )
    async with opened(None, recordings, **kw) as off:
        await process(off)
        before = outcome(off)
        assert off.ocr is not None
        calls = (len(off.ocr.sent), len(off.bedrock.requests))
        assert children_of(off) == [], "the flag is off: no child job"
    async with opened(ON, recordings, **kw) as on:
        await process(on)
        assert len(children_of(on)) == 2 * BATCHES, "the flag is on: the pages are read by child jobs"
        assert on.ocr is not None
        assert (len(on.ocr.sent), len(on.bedrock.requests)) == calls, "Azure and model call counts differ"
        after = outcome(on)
    assert len(before["words"]) > 0 and len(before["facts"]) > 0
    for key in before:
        assert after[key] == before[key], f"{key} differ between flag on and flag off"


async def test_children_run_without_the_case_lock_and_the_parent_is_woken_twice(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    async with opened(
        ON, recordings, admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, seed=seed, storage=storage
    ) as h:
        seen = await process(h, sample=True)
        parent = parent_of(h)
        kids = children_of(h)
        assert len(kids) == 2 * BATCHES
        phases = Counter(
            r[0] for r in h.admin.execute("select phase from app.pipeline_batches where run_job_id = %s", (parent,))
        )
        assert phases == {"ocr": BATCHES, "sort": BATCHES}
        rows = h.admin.execute(
            "select pj.lock, pj.queue_name from procrastinate.procrastinate_jobs pj"
            " where pj.args ->> 'job_id' = any(%s)",
            ([str(k) for k in kids],),
        ).fetchall()
        assert len(rows) == len(kids) and {r[0] for r in rows} == {None}, "a child took the case lock"
        assert {r[1] for r in rows} == {h.registry.queue_for(service.READ_PAGE_BATCH)}
        wakes = h.admin.execute(
            "select pj.lock from procrastinate.procrastinate_jobs pj where pj.args ->> 'job_id' = %s", (str(parent),)
        ).fetchall()
        assert len(wakes) == 3, "the parent runs three times: plan the reading, plan the sort, the tail"
        assert {r[0] for r in wakes} == {f"case:{h.case_id}"}
        status, attempts, pct, step = (
            h.admin.execute("select status, attempts, pct, step from app.jobs where id = %s", (parent,)).fetchone()
            or (None,) * 4
        )
        assert (status, attempts, pct, step) == ("done", 1, 100, "missing")
        run = h.admin.execute("select state from app.pipeline_runs where job_id = %s", (parent,)).fetchone()
        assert run == ("done",)
        ranks = [RANK[s] for s, _ in seen if s in RANK]
        assert ranks == sorted(ranks), "the step went back"
        pcts = [p for _, p in seen if p is not None]
        assert pcts == sorted(pcts), "the bar went back"


async def test_the_anchor_is_the_latest_printed_date_when_the_sort_was_planned(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fixed = dt.date(2019, 5, 17)
    asked: list[dt.date | None] = []

    async def latest(conn: Any, case_id: uuid.UUID) -> dt.date | None:
        asked.append(fixed)
        return fixed

    monkeypatch.setattr(service, "latest_printed_date", latest)
    async with opened(
        ON, recordings, admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, seed=seed, storage=storage
    ) as h:
        await process(h)
        row = h.admin.execute("select date_anchor from app.pipeline_runs where job_id = %s", (parent_of(h),)).fetchone()
        assert row == (fixed,)
        assert len(asked) == 1, "the bundle's latest date is asked once, when the sort is planned"


async def test_no_patient_text_in_jobs_runs_batches_or_logs(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    async with opened(
        ON, recordings, admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, seed=seed, storage=storage
    ) as h:
        await process(h)
        names = [n.lower() for n in expected("case.json")["case_names"]]
        text = " ".join(
            str(r[0]).lower()
            for table in ("app.jobs", "app.pipeline_runs", "app.pipeline_batches", "app.job_steps")
            for r in h.admin.execute(f"select to_jsonb(t) from {table} t where t.case_id = %s", (h.case_id,))  # noqa: S608
        )
        assert not any(n in text for n in names), "a name reached a job, run, batch or step row"
        logged = caplog.text.lower()
        assert not any(n in logged for n in names), "a name reached the logs"


async def test_progress_never_goes_back_and_a_quarantined_page_cannot_hold_the_bar_short(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    async with opened(
        ON, recordings, admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, seed=seed, storage=storage
    ) as h:
        await process(h)
        parent = parent_of(h)

        async def report() -> None:
            async with h.worker_db.job(h.case_id) as conn:
                await repo.report_progress(conn, parent)

        def parent_row() -> tuple[Any, ...]:
            row = h.admin.execute(
                "select step, pct, step_done, step_total from app.jobs where id = %s", (parent,)
            ).fetchone()
            assert row is not None
            return row

        # An older figure written late (the run is back in the OCR phase on paper): neither step nor bar go back.
        h.admin.execute(
            "update app.jobs set step = 'sort', pct = 50, step_done = 10, step_total = 18 where id = %s", (parent,)
        )
        h.admin.execute("update app.pipeline_runs set state = 'ocr_waiting' where job_id = %s", (parent,))
        await report()
        assert parent_row() == ("sort", 50, 10, 18)
        # One page set aside after the plan: the phase still counts as complete, so the bar reaches its band.
        victim = h.admin.execute(
            "select id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
            " order by bundle_page_no limit 1",
            (h.case_id,),
        ).fetchone()
        assert victim is not None
        h.admin.execute("update app.pages set quarantined = true where id = %s", (victim[0],))
        h.admin.execute(
            "update app.jobs set step = 'sort', pct = 40, step_done = null, step_total = null where id = %s", (parent,)
        )
        h.admin.execute("update app.pipeline_runs set state = 'sort_waiting' where job_id = %s", (parent,))
        await report()
        step, pct, done, total = parent_row()
        assert (step, pct) == ("sort", 55), "a quarantined page left the bar short of its band"
        assert done == total and total is not None


class Recording:
    """A connection that remembers the statements it was given, in order."""

    def __init__(self, conn: Any, log: list[str]) -> None:
        self._conn = conn
        self._log = log

    async def execute(self, query: Any, *args: Any, **kwargs: Any) -> Any:
        self._log.append(str(query))
        return await self._conn.execute(query, *args, **kwargs)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._conn, name)


async def test_a_child_completes_its_batch_before_it_touches_any_job_row(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The caller rule (reviewer, PR 17): `app.pipeline_complete_batch` takes the run, page and batch rows first and the
    job rows last, the order the sweep waits in, so a transaction that updated a job row before completing would
    deadlock with the sweep. Every transaction that completes a batch has it as its first statement."""
    transactions: list[list[str]] = []
    original = JobContext.transaction

    @contextlib.contextmanager
    def never() -> Iterator[None]:
        yield

    def recording(self: JobContext) -> Any:
        @contextlib.asynccontextmanager
        async def wrapped() -> AsyncIterator[Any]:
            log: list[str] = []
            transactions.append(log)
            async with original(self) as conn:
                yield Recording(conn, log)

        return wrapped()

    monkeypatch.setattr(JobContext, "transaction", recording)
    async with opened(
        ON, recordings, admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, seed=seed, storage=storage
    ) as h:
        await process(h)
    completing = [t for t in transactions if any("pipeline_complete_batch" in s for s in t)]
    assert len(completing) == 2 * BATCHES, "one completion per batch"
    for statements in completing:
        assert "pipeline_complete_batch" in statements[0], "a statement ran before the batch was completed"
        assert len(statements) == 1


async def test_the_heal_sweep_is_registered_and_hands_the_case_a_resumable_list(
    recordings: FixtureStore,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async with opened(
        ON, recordings, admin=admin, api_db=api_db, worker_dsn=worker_dsn, auth_env=auth_env, seed=seed, storage=storage
    ) as h:
        assert "pipeline_heal_runs" in h.registry.sweeps
        seen: list[Sequence[str]] = []

        async def heal(conn: Any, resumable: Sequence[str]) -> int:
            seen.append(list(resumable))
            return 0

        monkeypatch.setattr(repo, "heal_runs", heal)
        async with h.worker_db.anonymous() as conn:
            await h.registry.sweeps["pipeline_heal_runs"](conn)
        assert seen == [sorted(RESUMABLE_PAUSES)]
