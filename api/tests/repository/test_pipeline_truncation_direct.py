"""The direct provider end to end through the real kit: its own cut-off check (an answer that used the whole
`max_tokens`) reaches the pipeline's `answer_group`, a page too big even for the wider cap is given up once, and a
direct failure part-way through keeps the answers already gathered (only the failing part goes to Bedrock)."""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from typing import Any

import pytest

from app.core.ai.bedrock import ModelRequest, ModelResponse, ModelUnavailable
from app.core.ai.kit import AiKit
from app.core.ai.recordings import FixtureStore
from app.core.config import ANTHROPIC_LIMITS, BEDROCK_MAX_OUTPUT_TOKENS
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.features import pipeline
from app.features.pipeline.wiring import current
from tests.repository.test_pipeline_extract import _open, _wordy_lines, storage  # noqa: F401
from tests.repository.test_pipeline_kit_limits import opened_args  # noqa: F401
from tests.repository.test_pipeline_support import Harness, facts_of, text_pdf
from tests.support.ai import CELL_ENV, ScriptedBedrock, cell
from tests.support.packs import LAUNCH

pytestmark = pytest.mark.anyio

PAGES = 12
DIRECT = "claude-sonnet-4-6"
URL = "https://api.anthropic.com"

Needs = Callable[[list[int]], int]  # the tokens an answer for these pages would need


class Client:
    """A model stand-in: answers every page it is asked about with one fact covering the page's words, unless the
    answer would need more tokens than the request's cap, when it reports the whole cap used (cut off)."""

    def __init__(
        self,
        words: dict[int, Any],
        needs: Needs,
        *,
        endpoint: str,
        model_id: str | None,
        unavailable: int | None = None,
    ) -> None:
        self.words = words
        self.needs = needs
        self.endpoint = endpoint
        self.model_id = model_id
        self.unavailable = unavailable  # the direct API is gone for any call that includes this page
        self.calls: list[tuple[list[int], int]] = []  # page numbers, max_tokens

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        numbers = [int(n) for n in re.findall(r"=== PAGE (\d+) ===", request.text)]
        self.calls.append((numbers, request.max_tokens))
        if self.needs(numbers) > request.max_tokens:
            return ModelResponse(output={}, input_tokens=10, output_tokens=request.max_tokens, model_id=self.model_id)
        if self.unavailable in numbers:
            raise ModelUnavailable("gone")
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
        return ModelResponse(output={"pages": pages}, input_tokens=10, output_tokens=50, model_id=self.model_id)

    def asked(self) -> list[list[int]]:
        return [numbers for numbers, _ in self.calls]


async def run(
    opened: tuple[Any, ...], direct_needs: Needs, *, unavailable: int | None = None
) -> tuple[Client, Client, Harness, dict[str, str]]:
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
    direct = Client(words, direct_needs, endpoint=URL, model_id=DIRECT, unavailable=unavailable)
    bedrock = Client(words, lambda numbers: 1000 * len(numbers), endpoint=CELL_ENV["BEDROCK_ENDPOINT"], model_id=None)

    async def no_sleep(_seconds: float) -> None:
        return None

    settings = cell(
        AI_PRIMARY_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="sk-ant-test-not-real",
        AI_EXTRACTION_BATCH_SIZE="10",
        BEDROCK_RPM_LIMIT="1000",
    )
    kit = AiKit(settings, bedrock, direct=direct, sleep=no_sleep)
    async with ready as h:
        with pipeline.wiring.use(dataclasses.replace(current(), kit=kit)):
            await h.upload(document)
            finished = await h.drain()
        return direct, bedrock, h, finished


async def test_a_page_too_big_even_for_the_wider_cap_is_asked_exactly_twice_then_given_up(
    opened_args: tuple[Any, ...],  # noqa: F811
) -> None:
    dense = 3
    too_big = ANTHROPIC_LIMITS.widest_output_tokens + 1
    direct, bedrock, h, finished = await run(
        opened_args, lambda numbers: sum(too_big if n == dense else 1500 for n in numbers)
    )
    assert set(finished.values()) == {"done"}
    assert [tokens for numbers, tokens in direct.calls if numbers == [dense]] == [
        ANTHROPIC_LIMITS.max_output_tokens,
        ANTHROPIC_LIMITS.widest_output_tokens,
    ]
    assert len(facts_of(h)) == PAGES - 1  # only the page that fits nowhere is lost
    assert bedrock.calls == []


async def test_a_direct_failure_part_way_keeps_the_answers_found_and_only_the_failing_part_goes_to_bedrock(
    opened_args: tuple[Any, ...],  # noqa: F811
) -> None:
    # Five pages together are cut off (8,000 tokens each, more than the cap), so the batch is split: pages 1 and 2
    # are answered; the other three then meet a direct API that is gone for any call that holds page 5.
    direct, bedrock, h, finished = await run(opened_args, lambda numbers: 8000 * len(numbers), unavailable=5)
    assert set(finished.values()) == {"done"}
    assert len(facts_of(h)) == PAGES  # no page lost
    assert [numbers for numbers in direct.asked() if numbers == [1, 2]] == [[1, 2]]  # answered once, kept
    assert bedrock.asked() == [[3, 4, 5]]  # only the failing part is asked of Bedrock
    assert {tokens for _, tokens in bedrock.calls} == {BEDROCK_MAX_OUTPUT_TOKENS}
