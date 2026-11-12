"""Audit 13, reviewer P1: a page a parser child could not cut or draw is not given a blank stand-in as its page.

It has no stored page PDF, picture or thumbnail and its image stays unset, so the page is still "waiting for its
picture" and a later run cuts it again. It is marked unreadable meanwhile (the summary count and the clearer-copy
list), its thumbnail answers 404 instead of a blank image, and once a later run can cut it its words are read
like any other page's. A page that was never read and now has an image and words is readable again (the page guard,
migration 0074, allows exactly that clear); one that is recut but yields no words stays unreadable. Nothing
printed or asserted on is patient text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pikepdf
import pytest

from app.core import sandbox
from app.core.ai.ocr import OcrFailed
from app.core.config import SandboxSettings
from app.features.pipeline import parsing, wiring
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support import pipeline_parse_stand_ins as stand_in
from tests.support.file_lifecycle_documents import _save, _typed_page

pytestmark = pytest.mark.anyio

h = lifecycle.h
storage = lifecycle.storage

STEPS = {"cut_pages": "cut_exit", "pictures": "pictures_exit"}


@pytest.fixture(autouse=True)
async def short_timeout() -> AsyncIterator[None]:
    await sandbox.start(SandboxSettings(pool_size=2, timeout_seconds=6.0))
    yield
    await sandbox.stop()


def three_pages() -> bytes:
    pdf = pikepdf.new()
    for words in ("keep one", f"{stand_in.MARK} two", "keep three"):
        _typed_page(pdf, words)
    return _save(pdf)


def stored(h: Harness, page_id: Any) -> dict[str, Any]:
    row = h.admin.execute(
        "select image_path is not null, unreadable, text_source is not null,"
        " (select count(*) from app.ocr_words w where w.page_id = p.id) from app.pages p where id = %s",
        (page_id,),
    ).fetchone()
    assert row is not None
    return {"image": row[0], "unreadable": row[1], "read": row[2], "words": int(row[3])}


@pytest.mark.parametrize("step", list(STEPS))
async def test_a_lost_page_has_no_stand_in_picture_is_listed_and_is_cut_again_by_a_later_run(
    h: Harness, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    original = getattr(parsing, step)
    monkeypatch.setattr(parsing, step, getattr(stand_in, STEPS[step]))
    await lifecycle.run(h, three_pages(), "three.pdf")
    first, middle, last = h.pages()

    assert stored(h, middle["id"]) == {"image": False, "unreadable": True, "read": False, "words": 0}, (
        "a blank stand-in was stored as the page, or the page is not marked unreadable"
    )
    for page in (first, last):
        assert stored(h, page["id"]) == {
            "image": True,
            "unreadable": False,
            "read": True,
            "words": stored(h, page["id"])["words"],
        }
        assert stored(h, page["id"])["words"] > 0, "control"

    base = f"/v1/cases/{h.case_id}"
    assert (await h.client.get(f"{base}/pages/{middle['bundle_page_no']}/thumbnail")).status_code == 404
    summary = await h.client.get(f"{base}/bundle/summary")
    assert summary.status_code == 200 and summary.json()["unreadable"] == 1
    draft = await h.client.post(f"{base}/instructing-party-requests/draft", json={})
    assert draft.json()["clearer_copy_page_nos"] == [middle["bundle_page_no"]]

    monkeypatch.setattr(parsing, step, original)  # the parser works again: a later run cuts it
    await h.enqueue("process_bundle")
    assert set((await h.drain()).values()) <= {"done"}
    assert stored(h, middle["id"])["image"] is True, "not cut again"
    again = stored(h, middle["id"])
    # migration 0074: a page never read before, now with an image and words, is readable again
    assert again["unreadable"] is False and again["read"] is True and again["words"] > 0, again
    summary = await h.client.get(f"{base}/bundle/summary")
    assert summary.json()["unreadable"] == 0, "a recut and read page is still counted unreadable"


class NothingRead:
    """An OCR service that fails every page it is given (the page then has no words)."""

    endpoint = "synthetic"

    async def analyse(self, document: bytes, model: str = "layout", *, locale: str | None = None) -> Any:
        raise OcrFailed("synthetic")


async def test_a_lost_page_that_is_cut_again_but_yields_no_words_stays_unreadable(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    original = parsing.cut_pages
    monkeypatch.setattr(parsing, "cut_pages", stand_in.cut_exit)
    await lifecycle.run(h, three_pages(), "three.pdf")
    _, middle, _ = h.pages()
    monkeypatch.setattr(parsing, "cut_pages", original)
    monkeypatch.setattr(parsing, "text_layer", lambda page_pdf: None)  # no text layer: the page goes to OCR
    monkeypatch.setattr(wiring.current(), "ocr", NothingRead())
    await h.enqueue("process_bundle")
    await h.drain()
    again = stored(h, middle["id"])
    assert again["image"] is True and again["read"] is True and again["words"] == 0, again
    assert again["unreadable"] is True, "a page with no words must not be written readable"
