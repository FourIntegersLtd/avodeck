"""Embedding runs off the event loop, in batches (ADR 0029)."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Sequence

import pytest

from app.core.ai.embed import RecordedEmbedder, embed_batches
from app.core.ai.pii import Stripped

pytestmark = pytest.mark.anyio


def stripped(text: str) -> Stripped:
    return Stripped(text=text, counts={})


class SlowEmbedder(RecordedEmbedder):
    def __init__(self) -> None:
        self.calls: list[int] = []

    def embed(self, texts: Sequence[Stripped]) -> list[tuple[float, ...]]:
        self.calls.append(len(texts))
        time.sleep(0.3)  # a blocking forward pass
        return super().embed(texts)


async def test_the_event_loop_keeps_running_while_a_batch_is_embedded() -> None:
    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await asyncio.sleep(0.02)
            ticks += 1

    task = asyncio.create_task(ticker())
    await embed_batches(SlowEmbedder(), [stripped("one"), stripped("two")])
    task.cancel()
    assert ticks >= 5


async def test_texts_are_embedded_in_batches_of_32_in_order() -> None:
    embedder = SlowEmbedder()
    texts = [stripped(f"entry number {n}") for n in range(70)]
    vectors = await embed_batches(embedder, texts)
    assert embedder.calls == [32, 32, 6]
    assert vectors == RecordedEmbedder().embed(texts)


async def test_nothing_to_embed_makes_no_call() -> None:
    embedder = SlowEmbedder()
    assert await embed_batches(embedder, []) == []
    assert embedder.calls == []


async def test_queries_use_the_query_form_when_the_embedder_has_one() -> None:
    class Two(RecordedEmbedder):
        def embed_queries(self, texts: Sequence[Stripped]) -> list[tuple[float, ...]]:
            return [(1.0,)] * len(texts)

    assert await embed_batches(Two(), [stripped("q")], queries=True) == [(1.0,)]
