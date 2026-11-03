"""A dense page whose answer is cut off at the output cap still gets through: the batch is split, never asked
again with the same cap, and a page cut off on its own is asked once with the provider's wider cap."""

from __future__ import annotations

import dataclasses
from typing import Any

import pytest

from app.core.ai.kit import AiKit, OutputTruncated
from app.core.ai.recordings import FixtureStore
from app.core.config import ProviderLimits as Limits
from app.core.pdf import embedded_words, split_pages
from app.features import pipeline
from app.features.pipeline.wiring import current
from tests.repository.test_pipeline_extract import _open, _wordy_lines, storage  # noqa: F401
from tests.repository.test_pipeline_kit_limits import LimitedKit, opened_args  # noqa: F401
from tests.repository.test_pipeline_support import Harness, facts_of, text_pdf
from tests.support.ai import ScriptedBedrock, cell
from tests.support.packs import LAUNCH

pytestmark = pytest.mark.anyio

PAGES = 12
DENSE = 3  # this page's answer needs 12,000 tokens; every other page's needs 1,500


class CuttingKit(LimitedKit):
    """Answers like `LimitedKit`, except an answer that needs more tokens than the call's cap is cut off."""

    async def run_detached(self, database: Any, case_id: Any, task: Any, **kwargs: Any) -> dict[str, Any]:
        import re

        numbers = [int(n) for n in re.findall(r"=== PAGE (\d+) ===", kwargs["text"])]
        if sum(12000 if n == DENSE else 1500 for n in numbers) > task.max_tokens:
            self.calls.append((kwargs.get("provider"), numbers, task.max_tokens))
            raise OutputTruncated(task.task_id)
        return await super().run_detached(database, case_id, task, **kwargs)


async def run(opened: tuple[Any, ...], limits: Limits) -> tuple[CuttingKit, Harness, dict[str, str]]:
    tmp_path, admin, api_db, worker_dsn, auth_env, seed, case_storage = opened
    document = text_pdf([_wordy_lines(8) for _ in range(PAGES)])
    words = {n: embedded_words(p).words for n, p in enumerate(split_pages(document), start=1)}
    ready = await _open(
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
    async with ready as h:
        kit = CuttingKit(AiKit(cell(AI_EXTRACTION_BATCH_SIZE="10"), ScriptedBedrock([])), limits, words)
        with pipeline.wiring.use(dataclasses.replace(current(), kit=kit)):  # type: ignore[arg-type]
            await h.upload(document)
            finished = await h.drain()
        return kit, h, finished


async def test_a_dense_page_gets_through_by_splitting_and_then_a_wider_cap(opened_args: tuple[Any, ...]) -> None:  # noqa: F811
    kit, h, finished = await run(opened_args, Limits(1000, 5, 8000, 200, 16000))
    assert set(finished.values()) == {"done"}
    assert len(facts_of(h)) == PAGES  # no page lost
    assert [pages for _, pages, tokens in kit.calls if tokens == 16000] == [[DENSE]]
    alone = [tokens for _, pages, tokens in kit.calls if pages == [DENSE]]
    assert alone == [8000, 16000]  # cut off once at the cap, then asked once with the wider one


async def test_without_a_wider_cap_only_the_dense_page_is_lost(opened_args: tuple[Any, ...]) -> None:  # noqa: F811
    kit, h, finished = await run(opened_args, Limits(1000, 5, 8000, 200))
    assert set(finished.values()) == {"done"}
    assert len(facts_of(h)) == PAGES - 1
    assert [tokens for _, pages, tokens in kit.calls if pages == [DENSE]] == [8000]
