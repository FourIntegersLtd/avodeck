"""Audit 13, reviewer P4 and P5.

P4: a text-layer check that hangs or dies means "no usable text layer": the page goes to OCR like any scan and is
not marked unreadable for it. (No OCR is wired in this harness, so the page reaches the OCR step and the job pauses
for want of a model; that is the proof it was sent there.)

P5: the time a hostile document costs is bounded. Cuts that timed out are counted per document; past a small number
the document is marked damaged, the pages not yet cut are listed as lost, and nothing more is cut from it. The
sandbox is faked (the call raises `timed_out`), so no patient text is involved.
"""

from __future__ import annotations

from typing import Any

import pikepdf
import pytest

from app.core import sandbox
from app.features.pipeline import parsing
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support import pipeline_parse_stand_ins as stand_in
from tests.support.file_lifecycle_documents import _save, _typed_page

pytestmark = pytest.mark.anyio

h = lifecycle.h
storage = lifecycle.storage


def pdf(pages: int, marked: int | None = None) -> bytes:
    doc = pikepdf.new()
    for n in range(pages):
        _typed_page(doc, f"{stand_in.MARK} {n}" if n == marked else f"keep {n}")
    return _save(doc)


@pytest.mark.parametrize("mode", ["hang", "exit"])
async def test_p4_a_text_layer_check_that_fails_sends_the_page_to_ocr_and_does_not_mark_it_unreadable(
    h: Harness, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from app.core.config import SandboxSettings

    await sandbox.start(SandboxSettings(pool_size=2, timeout_seconds=6.0))
    monkeypatch.setattr(parsing, "text_layer", getattr(stand_in, f"layer_{mode}"))
    await lifecycle.run(h, pdf(3, marked=1), "three.pdf", allow_paused=True)
    first, middle, last = h.pages()
    assert middle["unreadable"] is False, "marked unreadable instead of being sent to OCR"
    assert middle["text_source"] is None, "stored as read although OCR never ran"
    paused = h.admin.execute(
        "select error_code from app.jobs where case_id = %s and status = 'paused'", (h.case_id,)
    ).fetchall()
    assert [r[0] for r in paused] == ["model_unavailable"], "the page did not reach the OCR step"
    await sandbox.stop()


async def test_p5_a_document_whose_cuts_keep_timing_out_is_marked_damaged_after_a_few(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = sandbox.run
    seen: list[int] = []

    async def hostile(fn: Any, *args: Any, timeout: float | None = None) -> Any:
        if fn is parsing.cut_pages:
            seen.append(len(seen))
            raise sandbox.SandboxError("timed_out")
        return await real(fn, *args, timeout=timeout)

    monkeypatch.setattr(sandbox, "run", hostile)
    await lifecycle.run(h, pdf(12), "twelve.pdf")
    assert len(seen) <= 3, f"{len(seen)} cuts were tried on one hostile document"
    document = h.admin.execute(
        "select unreadable_reason from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)",
        (h.case_id,),
    ).fetchone()
    assert document is not None and document[0] == "damaged"
    pages = h.pages()
    assert len(pages) == 12 and all(p["unreadable"] and p["image_path"] is None for p in pages), (
        "a page that was never cut is not listed as lost"
    )
