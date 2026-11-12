"""Audit 13, DP-09: a page lost to a parser child that hung or died is told to the expert like an OCR failure.

If its text check failed it has no words, is marked unreadable with OCR as its source and a confidence of zero. If
it could not be cut or drawn it has no picture, no words and no source (a later run cuts it again; see the recut
test), marked unreadable. It then appears wherever a page OCR could not read appears: the bundle summary's `unreadable`
count and the clearer-copy pages of the request to the instructing party. It is never a page read with no content,
and its neighbours are untouched. (A genuinely blank scan is also flagged by the same signal, so neither is
silently passed as read.) Nothing printed or asserted on is patient text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pikepdf
import pytest

from app.core import sandbox
from app.core.config import SandboxSettings
from app.features.pipeline import parsing
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support import pipeline_parse_stand_ins as stand_in
from tests.support.file_lifecycle_documents import _save, _typed_page

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage

STEPS = ["cut_pages", "pictures", "text_layer"]


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
        "select unreadable, text_source, ocr_confidence, (select count(*) from app.ocr_words w where w.page_id = p.id)"
        " from app.pages p where id = %s",
        (page_id,),
    ).fetchone()
    assert row is not None
    return {
        "unreadable": row[0],
        "source": row[1],
        "confidence": None if row[2] is None else float(row[2]),
        "words": int(row[3]),
    }


@pytest.mark.parametrize("step", STEPS)
async def test_a_page_lost_to_a_parser_failure_is_listed_as_a_page_we_could_not_read(
    h: Harness, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    standing = {"cut_pages": "cut_exit", "pictures": "pictures_exit", "text_layer": "layer_exit"}[step]
    monkeypatch.setattr(parsing, step, getattr(stand_in, standing))
    await lifecycle.run(h, three_pages(), "three.pdf")
    first, middle, last = h.pages()

    lost = stored(h, middle["id"])
    if step == "text_layer":  # the page is cut and drawn; only its text check failed: like an OCR failure
        expected = {"unreadable": True, "source": "ocr", "confidence": 0.0, "words": 0}
    else:  # DP-09 review P1: no stand-in is stored; the page is not read and is cut again by a later run
        expected = {"unreadable": True, "source": None, "confidence": None, "words": 0}
    assert lost == expected, "not marked unreadable the way its failed step should leave it"
    for page in (first, last):
        assert stored(h, page["id"])["unreadable"] is False and stored(h, page["id"])["words"] > 0, "control"

    summary = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert summary.status_code == 200 and summary.json()["unreadable"] == 1, "not counted as a page we could not read"

    draft = await h.client.post(f"/v1/cases/{h.case_id}/instructing-party-requests/draft", json={})
    assert draft.status_code == 200, draft.status_code
    assert draft.json()["clearer_copy_page_nos"] == [middle["bundle_page_no"]], "not listed for a clearer copy"
