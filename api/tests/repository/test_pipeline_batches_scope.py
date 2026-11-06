"""A page-batch child works on its page range only, and every page write goes through the guard (ADR 0038, step 7).

Given a `BatchScope` (the run, the batch, its page range and the plan-time date anchor):

- `_read_words`, `_extract` and `_embed` pass the range to the "what is left" queries, so only pages of the batch are
  touched, and call no function that looks across the whole case (`pages_without_language`, `reset_undone`,
  `latest_printed_date`). Without a scope they call them as today (the control that keeps the spies honest).
- `_load_page(anchor=...)` uses the anchor it is given, `None` included, and does not query the bundle's latest date.
- A refused guard (the run is closed) writes nothing at each of the store call sites; an allowed one writes as today.

Ids, counts and page numbers only (rule 6).
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.ai.embed import RecordedEmbedder
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import CaseFileStorage
from app.features.pipeline import repo, service
from app.features.pipeline.batches import BatchScope
from app.features.pipeline.wiring import PipelineServices, current
from tests.repository.test_pipeline_batches_support import add_batch, add_run, child_job, make_job
from tests.repository.test_pipeline_support import Harness, harness, text_pdf
from tests.support.ai import ScriptedBedrock, response
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PAGES = [[f"Synthetic {word} entry"] for word in ("alpha", "bravo", "charlie")]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


def answer(pack: Any) -> Any:
    first = embedded_words(split_pages(text_pdf(PAGES[:1]))[0]).words[0]
    return response(
        {
            "source_type": pack.source_types()[0]["key"],
            "printed_dates": [],
            "facts": [
                {
                    "kind": pack.fact_kinds()[0]["key"],
                    "clinician_role": None,
                    "fields": {},
                    "value": "Synthetic",
                    "word_ids": [first.id],
                }
            ],
            "references_out": [],
            "availability_evidence": [],
            "actions_requested_of_clinician": [],
            "entries": [],
        }
    )


@pytest.fixture
async def h(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    """A three-page bundle read, extracted and embedded in one job (today's path, flag off)."""
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    pack = default_library().combine(*LAUNCH.codes)
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([answer(pack)] * 40),
        names=[],
        embedder=RecordedEmbedder(),
    ) as running:
        await running.upload(text_pdf(PAGES))
        assert set((await running.drain()).values()) == {"done"}
        yield running


def scope(
    h: Harness,
    seed: Seed,
    *,
    first: int = 1,
    last: int = 2,
    state: str = "ocr_waiting",
    anchor: dt.date | None = None,
) -> tuple[JobContext, BatchScope]:
    """A child job of an open (or closed) run whose batch covers pages `first` to `last`."""
    parent = make_job(h.admin, seed, h.case_id)
    run = add_run(h.admin, h.case_id, parent, state)
    child = child_job(h.admin, seed, h.case_id, status="running")
    batch = add_batch(h.admin, run, h.case_id, 0, child=child, pages=last - first + 1)
    h.admin.execute(
        "update app.pipeline_batches set first_page_no = %s, last_page_no = %s where id = %s", (first, last, batch)
    )
    if state == "closed":
        h.admin.execute("update app.pipeline_runs set closed_at = now() where job_id = %s", (run,))
    ctx = JobContext(h.worker_db, child, h.case_id, "read_page_batch", 1)
    return ctx, BatchScope(run_job_id=run, batch_id=batch, first_page_no=first, last_page_no=last, date_anchor=anchor)


def forget_words(h: Harness) -> None:
    h.admin.execute(
        "update app.pages set text_source = null where bundle_id = (select bundle_id from app.cases where id = %s)",
        (h.case_id,),
    )
    h.admin.execute(
        "delete from app.ocr_words where page_id in (select id from app.pages where bundle_id ="
        " (select bundle_id from app.cases where id = %s))",
        (h.case_id,),
    )


def forget_extraction(h: Harness) -> None:
    ids = "select id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
    h.admin.execute(f"delete from app.facts where page_id in ({ids})", (h.case_id,))
    h.admin.execute(f"delete from app.page_analysis where page_id in ({ids})", (h.case_id,))


def forget_embeddings(h: Harness) -> None:
    h.admin.execute(
        "delete from app.page_embeddings where bundle_id = (select bundle_id from app.cases where id = %s)",
        (h.case_id,),
    )


def read_numbers(h: Harness) -> list[int]:
    return [
        int(r[0])
        for r in h.admin.execute(
            "select bundle_page_no from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
            " and text_source is not null order by 1",
            (h.case_id,),
        )
    ]


def extracted_numbers(h: Harness) -> list[int]:
    return [
        int(r[0])
        for r in h.admin.execute(
            "select p.bundle_page_no from app.pages p join app.page_analysis a on a.page_id = p.id"
            " where p.bundle_id = (select bundle_id from app.cases where id = %s) and a.extracted_at is not null"
            " order by 1",
            (h.case_id,),
        )
    ]


def embedded_numbers(h: Harness) -> list[int]:
    return [
        int(r[0])
        for r in h.admin.execute(
            "select p.bundle_page_no from app.pages p join app.page_embeddings e on e.page_id = p.id"
            " where p.bundle_id = (select bundle_id from app.cases where id = %s) order by 1",
            (h.case_id,),
        )
    ]


class Spies:
    """Names of the case-wide functions called, in order."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.called: list[str] = []
        for name in ("reset_undone", "pages_without_language"):
            self._wrap(monkeypatch, repo, name)
        self._wrap(monkeypatch, service, "latest_printed_date")

    def _wrap(self, monkeypatch: pytest.MonkeyPatch, owner: Any, name: str) -> None:
        original = getattr(owner, name)

        async def spy(*args: Any, **kwargs: Any) -> Any:
            self.called.append(name)
            return await original(*args, **kwargs)

        monkeypatch.setattr(owner, name, spy)


@pytest.fixture
def spies(monkeypatch: pytest.MonkeyPatch) -> Spies:
    return Spies(monkeypatch)


def services_of(h: Harness) -> PipelineServices:
    return current()


# --- reading words ---------------------------------------------------------------------------------------------


async def test_a_child_reads_the_words_of_its_range_only(h: Harness, seed: Seed, spies: Spies) -> None:
    forget_words(h)
    ctx, batch = scope(h, seed, first=1, last=2)
    await service._read_words(ctx, services_of(h), batch)
    assert read_numbers(h) == [1, 2]
    assert "pages_without_language" not in spies.called


async def test_without_a_scope_the_words_are_read_for_every_page_and_languages_named(
    h: Harness, seed: Seed, spies: Spies
) -> None:
    forget_words(h)
    ctx = JobContext(h.worker_db, seed.job(h.case_id), h.case_id, "process_bundle", 1)
    await service._read_words(ctx, services_of(h))
    assert read_numbers(h) == [1, 2, 3]
    assert "pages_without_language" in spies.called


async def test_a_closed_run_refuses_the_words_and_nothing_is_written(h: Harness, seed: Seed) -> None:
    forget_words(h)
    ctx, batch = scope(h, seed, state="closed")
    await service._read_words(ctx, services_of(h), batch)
    assert read_numbers(h) == []
    count = h.admin.execute(
        "select count(*) from app.ocr_words w join app.pages p on p.id = w.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s)",
        (h.case_id,),
    ).fetchone()
    assert count == (0,)


# --- extracting ------------------------------------------------------------------------------------------------


async def test_a_child_extracts_its_range_only_and_asks_nothing_about_the_whole_case(
    h: Harness, seed: Seed, spies: Spies
) -> None:
    forget_extraction(h)
    ctx, batch = scope(h, seed, first=1, last=2, state="sort_waiting", anchor=dt.date(2020, 1, 1))
    await service._extract(ctx, services_of(h), batch)
    assert extracted_numbers(h) == [1, 2]
    assert spies.called == []


async def test_without_a_scope_the_extraction_looks_across_the_case(h: Harness, seed: Seed, spies: Spies) -> None:
    forget_extraction(h)
    ctx = JobContext(h.worker_db, seed.job(h.case_id), h.case_id, "process_bundle", 1)
    await service._extract(ctx, services_of(h))
    assert extracted_numbers(h) == [1, 2, 3]
    assert {"reset_undone", "latest_printed_date"} <= set(spies.called)


async def test_a_closed_run_refuses_the_extraction_and_nothing_is_written(h: Harness, seed: Seed) -> None:
    forget_extraction(h)
    ctx, batch = scope(h, seed, state="closed")
    await service._extract(ctx, services_of(h), batch)
    assert extracted_numbers(h) == []
    count = h.admin.execute(
        "select count(*) from app.facts f join app.pages p on p.id = f.page_id"
        " where p.bundle_id = (select bundle_id from app.cases where id = %s)",
        (h.case_id,),
    ).fetchone()
    assert count == (0,)


async def test_a_page_given_an_anchor_uses_it_and_does_not_ask_for_the_latest_date(
    h: Harness, seed: Seed, spies: Spies
) -> None:
    services = services_of(h)
    page = uuid.UUID(str(h.pages()[0]["id"]))
    async with h.worker_db.job(h.case_id) as conn:
        pack = services.packs.for_case(await repo.case_codes(conn, h.case_id))
        who = await services.identity(conn, h.case_id)
    ctx = JobContext(h.worker_db, seed.job(h.case_id), h.case_id, "read_page_batch", 1)
    given = await service._load_page(ctx, services, pack, who, page, anchor=dt.date(2019, 5, 6))
    none_planned = await service._load_page(ctx, services, pack, who, page, anchor=None)
    assert given.latest == dt.date(2019, 5, 6)
    assert none_planned.latest is None
    assert spies.called == []


async def test_a_page_without_an_anchor_still_asks_for_the_latest_date(h: Harness, seed: Seed, spies: Spies) -> None:
    services = services_of(h)
    page = uuid.UUID(str(h.pages()[0]["id"]))
    async with h.worker_db.job(h.case_id) as conn:
        pack = services.packs.for_case(await repo.case_codes(conn, h.case_id))
        who = await services.identity(conn, h.case_id)
    ctx = JobContext(h.worker_db, seed.job(h.case_id), h.case_id, "process_bundle", 1)
    await service._load_page(ctx, services, pack, who, page)
    assert spies.called == ["latest_printed_date"]


# --- embedding -------------------------------------------------------------------------------------------------


async def test_a_child_embeds_its_range_only(h: Harness, seed: Seed, spies: Spies) -> None:
    forget_embeddings(h)
    ctx, batch = scope(h, seed, first=2, last=3, state="sort_waiting")
    await service._embed(ctx, services_of(h), batch)
    assert embedded_numbers(h) == [2, 3]


async def test_a_closed_run_refuses_the_embeddings_and_nothing_is_written(h: Harness, seed: Seed) -> None:
    forget_embeddings(h)
    ctx, batch = scope(h, seed, state="closed")
    await service._embed(ctx, services_of(h), batch)
    assert embedded_numbers(h) == []


# --- a closed run ends the child (review of #288: a refusal because the run is gone is not one skipped page) -----


class GuardCalls:
    """How many page writes asked the guard: with one page in flight at a time, each is a page of work done."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.asked = 0
        original = repo.batch_write_allowed

        async def spy(*args: Any, **kwargs: Any) -> bool:
            self.asked += 1
            return await original(*args, **kwargs)

        monkeypatch.setattr(repo, "batch_write_allowed", spy)


async def test_a_closed_run_ends_the_child_at_the_first_refused_word_write(
    h: Harness, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    forget_words(h)
    monkeypatch.setattr(service, "ocr_concurrency", lambda services: 1)
    guard = GuardCalls(monkeypatch)
    ctx, batch = scope(h, seed, first=1, last=3, state="closed")
    await service._read_words(ctx, services_of(h), batch)
    assert guard.asked == 1, f"{guard.asked} pages were read for a run that was already closed"
    assert read_numbers(h) == []


async def test_a_closed_run_ends_the_child_at_the_first_refused_extraction(
    h: Harness, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    forget_extraction(h)
    monkeypatch.setattr(service, "_extract_concurrency", lambda ctx, services: 1)
    monkeypatch.setattr(service.extract, "pages_per_call", lambda *args, **kwargs: 1)
    guard = GuardCalls(monkeypatch)
    ctx, batch = scope(h, seed, first=1, last=3, state="closed")
    await service._extract(ctx, services_of(h), batch)
    assert guard.asked == 1, f"{guard.asked} model calls were made for a run that was already closed"
    assert extracted_numbers(h) == []


async def test_a_closed_run_ends_the_child_at_the_first_refused_embedding(
    h: Harness, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    forget_embeddings(h)
    monkeypatch.setattr(service.embed, "BATCH", 1)
    guard = GuardCalls(monkeypatch)
    ctx, batch = scope(h, seed, first=1, last=3, state="closed")
    await service._embed(ctx, services_of(h), batch)
    assert guard.asked == 1, f"{guard.asked} pages were embedded for a run that was already closed"
    assert embedded_numbers(h) == []


async def test_a_page_set_aside_is_skipped_and_the_child_carries_on(
    h: Harness, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The control: one refused page (not the run) skips that page only."""
    forget_embeddings(h)
    monkeypatch.setattr(service.embed, "BATCH", 1)
    guard = GuardCalls(monkeypatch)
    ctx, batch = scope(h, seed, first=1, last=3, state="sort_waiting")
    h.admin.execute(
        "update app.pages set superseded_by_page_id = (select id from app.pages where bundle_page_no = 3"
        " and bundle_id = (select bundle_id from app.cases where id = %s)) where bundle_page_no = 1"
        " and bundle_id = (select bundle_id from app.cases where id = %s)",
        (h.case_id, h.case_id),
    )
    await service._embed(ctx, services_of(h), batch)
    assert guard.asked == 3
    assert embedded_numbers(h) == [2, 3]
