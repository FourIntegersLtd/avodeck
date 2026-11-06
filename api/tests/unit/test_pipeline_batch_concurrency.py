"""A page-batch job stays inside its share of the worker's pool (ADR 0038, "Bounds on concurrency").

With the flag on, the parent and every child call `ctx.limit_checkouts(cap)`, with `cap` the pool less its reserve
shared by the jobs the worker runs at once (a pool of 10, reserve 2, four jobs: 2). Every `bounded()` width in that
mode is at most `cap` too, so pages in flight stay modest at each level. A task never takes a second checkout while it
holds one (`CheckoutSpy.nested`), or the limiter could deadlock. With the flag off nothing is capped, as today.
Messages carry counts only (rule 6).
"""

from __future__ import annotations

import contextlib
import datetime as dt
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.config import PipelineBatchSettings
from app.core.db import Database
from app.core.jobs import checkout_cap
from app.core.storage import CaseFileStorage
from app.features.pipeline import service
from tests.repository.test_pipeline_support import (
    Harness,
    expected,
    harness,
    recorded_book,
    rekeyed_store,
    subset_document,
    subset_page_bytes,
)
from tests.support.checkouts import CheckoutSpy
from tests.support.files import local_storage
from tests.support.jobs import FINISHED, open_worker, run_until_finished
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ON = PipelineBatchSettings(pages=5, window=2, min_pages=1)
POOL = 10  # the worker's pool in this test
CONCURRENCY = 4  # jobs the worker runs at once (the default `WORKER_CONCURRENCY`)
CAP = checkout_cap(pool_max=POOL, concurrency=CONCURRENCY)


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
        pool_max=POOL,
    ) as h:
        yield h


def widths_of_bounded(monkeypatch: pytest.MonkeyPatch, *, when: Callable[[], bool] = lambda: True) -> list[int]:
    """Every width `bounded()` is asked for while it has something to do and `when()` holds."""
    seen: list[int] = []
    real = service.bounded

    async def spy(
        items: Sequence[Any],
        work: Callable[[Any], Awaitable[None]],
        limit: int = service.CONCURRENCY,
        stop: Any = None,
        *,
        label: Any = None,
    ) -> None:
        if items and when():
            seen.append(limit)
        await real(items, work, limit, stop, label=label)

    monkeypatch.setattr(service, "bounded", spy)
    return seen


async def run_bundle(h: Harness) -> None:
    """Upload the subset and run the worker, four jobs at once, until every job of the case has finished."""
    await h.upload(subset_document())
    async with open_worker(h.registry, h.worker_db, h.worker_dsn) as worker:
        while True:
            ids = h.job_ids()
            found = await run_until_finished(worker, h.admin, ids, concurrency=CONCURRENCY, timeout=300)
            if len(h.job_ids()) == len(ids) and all(s in FINISHED for s in found.values()):
                break
    assert set(found.values()) == {"done"}, found


async def test_a_batch_mode_bundle_holds_at_most_its_cap_and_every_width_is_within_it(
    recordings: FixtureStore, admin: psycopg.Connection, api_db: Database, worker_dsn: str,
    auth_env: dict[str, str], seed: Seed, storage: CaseFileStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    assert CAP == 2, "a pool of 10, reserve 2, four jobs at once"
    async with opened(
        ON,
        recordings,
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    ) as h:
        spy = CheckoutSpy(h.worker_db).install(monkeypatch)

        def a_run_exists() -> bool:  # the split, before any run, is the old job's step and is not capped
            row = h.admin.execute("select 1 from app.pipeline_runs where case_id = %s", (h.case_id,)).fetchone()
            return row is not None

        widths = widths_of_bounded(monkeypatch, when=a_run_exists)
        await run_bundle(h)
    assert spy.total > 0, "the parent and the children asked for a limit"
    assert spy.peak <= ON.window * CAP + CAP, "one bundle: its window of children plus the parent, each within its cap"
    assert spy.nested == 0, "no task took a second checkout while it held one"
    assert widths and max(widths) <= CAP, f"widest bounded() in batch mode: {max(widths)}"


async def test_without_the_flag_nothing_is_capped_as_today(
    recordings: FixtureStore, admin: psycopg.Connection, api_db: Database, worker_dsn: str,
    auth_env: dict[str, str], seed: Seed, storage: CaseFileStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    async with opened(
        None,
        recordings,
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    ) as h:
        spy = CheckoutSpy(h.worker_db).install(monkeypatch)
        widths = widths_of_bounded(monkeypatch)
        await run_bundle(h)
    assert spy.total == 0, "no limit was set"
    assert max(widths) > CAP, "the single job keeps its own widths"


async def test_a_bundle_too_small_to_batch_reads_in_line_at_its_own_widths_with_the_flag_on(
    recordings: FixtureStore, admin: psycopg.Connection, api_db: Database, worker_dsn: str,
    auth_env: dict[str, str], seed: Seed, storage: CaseFileStorage, monkeypatch: pytest.MonkeyPatch,
) -> None:  # fmt: skip
    """With the flag on but no run planned (the bundle is under `min_pages`), the job is the old single job: it is
    not capped, or its OCR would drop from the usual width to the cap."""
    too_big_a_minimum = PipelineBatchSettings(pages=5, window=2, min_pages=100_000)
    async with opened(
        too_big_a_minimum,
        recordings,
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    ) as h:
        spy = CheckoutSpy(h.worker_db).install(monkeypatch)
        widths = widths_of_bounded(monkeypatch)
        await run_bundle(h)
        runs = h.admin.execute("select count(*) from app.pipeline_runs where case_id = %s", (h.case_id,)).fetchone()
    assert runs == (0,), "a run was planned for a bundle under the minimum"
    assert spy.total == 0, "an in-line job was given a checkout limit"
    assert max(widths) > CAP, "the in-line job's widths were cut to the cap"
