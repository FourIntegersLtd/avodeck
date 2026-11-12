"""AUDIT-13 DP-01 (pipeline half): one page is sent to OCR, so the answer must be exactly one page numbered 1.

A fake service answers with no pages, with two pages, or with one page numbered 2 for one scanned page. In each
case that page alone becomes unreadable exactly as an `OcrFailed` page does (F4): no words from the wrong page are
stored, the job does not fail and every other page keeps its words. Nothing here prints page text.
"""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable
from typing import Any

import pytest

from app.core.ai.ocr import OcrModel, OcrPage, OcrResult
from tests.repository.test_pipeline_ocr_failures import _count, opened, scanned_hashes  # noqa: F401
from tests.repository.test_pipeline_support import Harness, subset_document, subset_entries
from tests.repository.test_pipeline_words import _open, recordings, storage  # noqa: F401

pytestmark = pytest.mark.anyio

Shape = Callable[[tuple[OcrPage, ...]], tuple[OcrPage, ...]]

SHAPES: dict[str, Shape] = {
    "no pages": lambda pages: (),
    "two pages": lambda pages: (pages[0], dataclasses.replace(pages[0], number=2)),
    "page numbered 2": lambda pages: (dataclasses.replace(pages[0], number=2),),
}


class Misshapen:
    """Recorded OCR whose answer for the pages whose SHA-256 is in `bad` is reshaped."""

    def __init__(self, inner: Any, shape: Shape, bad: set[str]) -> None:
        self.inner, self.shape, self.bad = inner, shape, bad
        self.endpoint = inner.endpoint

    async def analyse(self, document: bytes, model: OcrModel = "layout", *, locale: str | None = None) -> OcrResult:
        result: OcrResult = await self.inner.analyse(document, model)
        if hashlib.sha256(document).hexdigest() in self.bad:
            return OcrResult(model=result.model, pages=self.shape(result.pages))
        return result


@pytest.mark.parametrize("name", list(SHAPES))
async def test_an_answer_that_is_not_one_page_numbered_1_makes_that_page_unreadable_and_the_job_carries_on(
    opened: Harness,  # noqa: F811
    name: str,
) -> None:
    (broken_no, broken), *rest = scanned_hashes()
    assert opened.ocr is not None
    counting: Any = opened.ocr
    counting.inner = Misshapen(counting.inner, SHAPES[name], {broken})
    await opened.upload(subset_document())
    finished = await opened.drain(timeout=300)
    assert set(finished.values()) == {"done"}, finished
    numbers = [int(e["page"]) for e in subset_entries()]
    pages = dict(zip(numbers, opened.pages(), strict=True))
    assert pages[broken_no]["unreadable"] is True
    assert _count(opened, pages[broken_no]["id"]) == 0, "words from a wrongly shaped answer were stored"
    for n, _ in rest:
        assert _count(opened, pages[n]["id"]) > 0, f"page {n} lost its words"
