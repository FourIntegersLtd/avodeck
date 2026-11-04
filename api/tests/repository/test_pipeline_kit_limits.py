"""Extraction follows the model kit's own per-provider limits (ADR 0036): how many pages go in one call, the
answer size asked for, how many calls run at once, and a re-split onto Bedrock when the direct API fails on a
call too big for it. The kit here wraps the real one, with limits and failures scripted."""

from __future__ import annotations

import asyncio
import dataclasses
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest

from app.core.ai.kit import AiKit, TooLargeForFallback
from app.core.ai.recordings import FixtureStore
from app.core.config import ProviderLimits as Limits
from app.core.db import Database
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import CaseFileStorage
from app.features import pipeline
from app.features.pipeline import extract
from app.features.pipeline.wiring import current
from tests.repository.test_pipeline_extract import _open, _wordy_lines, storage  # noqa: F401
from tests.repository.test_pipeline_support import Harness, facts_of, text_pdf
from tests.support.ai import ScriptedBedrock, cell
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PAGES = 12
POOL = 20  # the worker's connection pool for these runs (the harness's own holds four)
RESERVE = 2  # connections kept free for heartbeats, progress writes and the job's own transaction


class LimitedKit:
    """Answers every page it is asked about with one fact covering the page's words, and records each call."""

    def __init__(
        self, real: AiKit, limits: Limits, words: dict[int, Any], *, primary_fails_above: int | None = None
    ) -> None:
        self.settings = real.settings
        self.primary_limits = limits
        self.words = words
        self.primary_fails_above = primary_fails_above
        self.calls: list[tuple[str | None, list[int], int]] = []  # provider, page numbers, max_tokens
        self.in_flight = 0
        self.peak = 0

    async def run_detached(self, database: Any, case_id: Any, task: Any, **kwargs: Any) -> dict[str, Any]:
        provider = kwargs.get("provider")
        numbers = [int(n) for n in re.findall(r"=== PAGE (\d+) ===", kwargs["text"])]
        self.calls.append((provider, numbers, task.max_tokens))
        if provider is None and self.primary_fails_above is not None and len(numbers) > self.primary_fails_above:
            raise TooLargeForFallback(pages_per_call=self.primary_fails_above)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.05)
        finally:
            self.in_flight -= 1
        pack = default_library().combine(*LAUNCH.codes)
        source = pack.source_types()[-1]["key"]
        kind = pack.fact_kinds()[-1]["key"]
        pages = []
        for number in numbers:
            words = self.words[number]
            fact = {
                "kind": kind,
                "clinician_role": None,
                "fields": {},
                "value": " ".join(w.text for w in words),
                "word_ids": [w.id for w in words],
            }
            pages.append(
                {
                    "page_no": number,
                    "source_type": source,
                    "printed_dates": [],
                    "facts": [fact],
                    "references_out": [],
                    "availability_evidence": [],
                    "actions_requested_of_clinician": [],
                    "entries": [],
                }
            )
        return {"pages": pages}


async def run(
    opened_args: tuple[Any, ...],
    limits: Limits,
    *,
    primary_fails_above: int | None = None,
    pages: int = PAGES,
    pool: int = POOL,
) -> tuple[LimitedKit, Harness, dict[str, str]]:
    tmp_path, admin, api_db, worker_dsn, auth_env, seed, case_storage = opened_args
    document = text_pdf([_wordy_lines(8) for _ in range(pages)])
    words = {n: embedded_words(p).words for n, p in enumerate(split_pages(document), start=1)}
    opened = await _open(
        LAUNCH,
        FixtureStore(tmp_path / "nothing-recorded"),
        ScriptedBedrock([RuntimeError("the stand-in kit answers; Bedrock is not asked")]),
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=case_storage,
        batch_size="10",
    )
    async with opened as h:
        sized = Database(worker_dsn, max_size=pool)
        await sized.open()
        h.worker_db = sized
        kit = LimitedKit(
            AiKit(cell(AI_EXTRACTION_BATCH_SIZE="10"), ScriptedBedrock([])),
            limits,
            words,
            primary_fails_above=primary_fails_above,
        )
        try:
            with pipeline.wiring.use(dataclasses.replace(current(), kit=kit)):  # type: ignore[arg-type]
                await h.upload(document)
                finished = await h.drain()
        finally:
            await sized.close()
        return kit, h, finished


@pytest.fixture
async def opened_args(
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,  # noqa: F811
) -> AsyncIterator[tuple[Any, ...]]:
    yield (tmp_path, admin, api_db, worker_dsn, auth_env, seed, storage)


def sizes(kit: LimitedKit, provider: str | None) -> list[int]:
    return sorted(len(pages) for who, pages, _ in kit.calls if who == provider and pages)


async def test_a_call_holds_at_most_the_primary_providers_pages_per_call(opened_args: tuple[Any, ...]) -> None:
    kit, h, finished = await run(opened_args, Limits(1000, 5, 8000, 200))
    assert set(finished.values()) == {"done"}
    assert sizes(kit, None) == [2, 5, 5]  # 12 pages, configured size 10, the provider's limit 5
    assert len(facts_of(h)) == PAGES


async def test_the_answer_size_asked_for_is_the_primary_providers_limit(opened_args: tuple[Any, ...]) -> None:
    kit, _, _ = await run(opened_args, Limits(1000, 5, 8000, 200))
    assert {tokens for _, _, tokens in kit.calls} == {8000}


async def test_calls_run_as_many_at_once_as_the_primary_provider_allows(opened_args: tuple[Any, ...]) -> None:
    kit, _, _ = await run(opened_args, Limits(1000, 2, 8000, 4))
    assert len(kit.calls) == 6
    assert kit.peak == 4


async def test_a_call_too_big_for_the_fallback_is_resplit_and_asked_of_bedrock(opened_args: tuple[Any, ...]) -> None:
    kit, h, finished = await run(opened_args, Limits(1000, 5, 8000, 3), primary_fails_above=2)
    assert set(finished.values()) == {"done"}
    assert sizes(kit, "bedrock") == [1, 1, 2, 2, 2, 2]  # two 5-page calls re-split at 2; the 2-page call fit
    assert len(facts_of(h)) == PAGES  # no page lost
    assert {tokens for who, _, tokens in kit.calls if who == "bedrock"} == {extract.BATCH_MAX_TOKENS}


def test_a_kit_limit_replaces_the_fixed_batch_size_without_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    assert extract.pages_per_call(10, 5) == 5
    assert extract.pages_per_call(3, 5) == 3
    assert extract.pages_per_call(10) == extract.MAX_PAGES_PER_CALL
    assert [r for r in caplog.records if r.levelname == "WARNING" and "5" in r.getMessage()] == []


def test_a_bedrock_sized_limit_keeps_the_fixed_ten_page_cap(caplog: pytest.LogCaptureFixture) -> None:
    assert extract.pages_per_call(20, 10) == 10  # Bedrock as primary, size 20 configured: 16,000 tokens out
    assert extract.pages_per_call(20, 50) == extract.MAX_PAGES_PER_CALL
    assert extract.pages_per_call(10, 5) == 5


async def test_calls_in_flight_never_exceed_the_worker_pool_less_its_reserve_and_the_step_log_says_so(
    opened_args: tuple[Any, ...], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    kit, _, _ = await run(opened_args, Limits(1000, 1, 8000, 200), pages=30)
    assert len(kit.calls) == 30
    assert 3 < kit.peak <= POOL - RESERVE  # a timing-dependent ceiling; the step log below states the bound
    starts = [r.getMessage() for r in caplog.get_records("call") if "step=sort event=start" in r.getMessage()]
    assert starts and f"concurrency={POOL - RESERVE}" in starts[0]


async def test_a_smaller_worker_pool_means_fewer_calls_in_flight(
    opened_args: tuple[Any, ...], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level("INFO")
    kit, _, finished = await run(opened_args, Limits(1000, 1, 8000, 200), pages=20, pool=6)
    assert set(finished.values()) == {"done"}
    assert kit.peak <= 6 - RESERVE
    starts = [r.getMessage() for r in caplog.get_records("call") if "step=sort event=start" in r.getMessage()]
    assert starts and f"concurrency={6 - RESERVE}" in starts[0]


async def test_a_tiny_worker_pool_still_makes_one_call_at_a_time(opened_args: tuple[Any, ...]) -> None:
    kit, _, finished = await run(opened_args, Limits(1000, 2, 8000, 200), pool=1)
    assert set(finished.values()) == {"done"}
    assert kit.peak == 1
