"""Audit 13, DP-04: a document and a bundle have page caps (`MAX_DOCUMENT_PAGES`, `MAX_BUNDLE_PAGES`).

A document over the per-document cap, or one that would take the bundle over its cap, is marked
`unreadable_reason = 'too_many_pages'`, gets no pages and is listed with the files we could not open; the cut is
never run for it and every other document is numbered as usual. Files are made up here.
"""

from __future__ import annotations

import dataclasses

import pytest

from app.features.pipeline import parsing
from app.features.pipeline import service as pipeline_service
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support.file_lifecycle_documents import blank_pages_pdf, text_pdf

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage
run, state = lifecycle.run, lifecycle.state


def capped(monkeypatch: pytest.MonkeyPatch, *, document: int, bundle: int) -> None:
    monkeypatch.setattr(
        pipeline_service,
        "LIMITS",
        dataclasses.replace(pipeline_service.LIMITS, max_document_pages=document, max_bundle_pages=bundle),
    )


async def unopened(h: Harness) -> list[dict[str, str]]:
    summary = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert summary.status_code == 200
    found: list[dict[str, str]] = summary.json()["unopened_documents"]
    return found


def test_the_count_refuses_a_pdf_over_the_document_cap_before_any_page_is_cut(monkeypatch: pytest.MonkeyPatch) -> None:
    capped(monkeypatch, document=3, bundle=100)
    cut: list[int] = []
    monkeypatch.setattr(parsing, "cut_page", lambda *a, **k: cut.append(1))
    assert pipeline_service._checked_pages(blank_pages_pdf(3)) == 3, "control: a PDF at the cap is counted"
    cut.clear()
    with pytest.raises(pipeline_service.TooManyPages):
        pipeline_service._checked_pages(blank_pages_pdf(4))
    assert cut == [], "pages were cut from a document already known to be over the cap"


async def test_a_document_over_the_page_cap_is_listed_not_opened_and_the_others_go_on(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    capped(monkeypatch, document=3, bundle=100)
    big = await run(h, text_pdf(4, "over"), "big.pdf")
    small = await run(h, text_pdf(2, "after"), "small.pdf")
    assert state(h, big)["unreadable_reason"] == "too_many_pages" and state(h, big)["page_rows"] == 0
    assert state(h, small)["unreadable_reason"] is None and state(h, small)["page_rows"] == 2, "control"
    assert [d["unreadable_reason"] for d in await unopened(h)] == ["too_many_pages"]


async def test_a_document_that_would_take_the_bundle_over_its_cap_is_listed_and_a_later_small_one_still_fits(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    capped(monkeypatch, document=4, bundle=5)
    first = await run(h, text_pdf(3, "first"), "first.pdf")
    second = await run(h, text_pdf(3, "second"), "second.pdf")  # 3 + 3 > 5
    third = await run(h, text_pdf(2, "third"), "third.pdf")  # 3 + 2 = 5, exactly the cap
    assert state(h, first)["page_rows"] == 3 and state(h, first)["unreadable_reason"] is None, "control"
    assert state(h, second)["unreadable_reason"] == "too_many_pages" and state(h, second)["page_rows"] == 0
    assert state(h, third)["unreadable_reason"] is None and state(h, third)["page_rows"] == 2
    assert len(h.pages()) == 5, "the bundle is numbered up to its cap and no further"
