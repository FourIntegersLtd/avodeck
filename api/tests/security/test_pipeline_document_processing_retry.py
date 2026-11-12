"""Audit 13, reviewer P2: a parser failure is the file's fault only when it is the file's doing.

`unavailable` (no child could serve the call) and `stopped` (the pool was stopping) say nothing about the file: the
job fails and is retried, and the document or page is not marked. `timed_out` and `crashed` get one more try in a
fresh child before the document is marked damaged. The page-count timeout grows with the file's size, within a bound.
The sandbox is faked here (the call raises the code; nothing runs in a child), so no patient text is involved.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pikepdf
import pytest

from app.core import sandbox
from app.features.pipeline import parser_calls, parsing
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support.file_lifecycle_documents import _save, _typed_page

pytestmark = pytest.mark.anyio

h = lifecycle.h
storage = lifecycle.storage


def pdf(pages: int = 3) -> bytes:
    doc = pikepdf.new()
    for n in range(pages):
        _typed_page(doc, f"keep {n}")
    return _save(doc)


def script(monkeypatch: pytest.MonkeyPatch, name: str, codes: Callable[[int], str | None]) -> list[int]:
    """`parsing.<name>` calls raise `codes(call number)` (None: run for real). Returns the call numbers seen."""
    real = sandbox.run
    seen: list[int] = []
    target = getattr(parsing, name)

    async def fake(fn: Any, *args: Any, timeout: float | None = None) -> Any:
        if fn is target:
            n = len(seen)
            seen.append(n)
            code = codes(n)
            if code is not None:
                raise sandbox.SandboxError(code)
        return await real(fn, *args, timeout=timeout)

    monkeypatch.setattr(sandbox, "run", fake)
    return seen


def document_state(h: Harness) -> tuple[str | None, int]:
    row = h.admin.execute(
        "select d.unreadable_reason, (select count(*) from app.pages p where p.document_id = d.id)"
        " from app.documents d where d.bundle_id = (select bundle_id from app.cases where id = %s)"
        " order by d.created_at limit 1",
        (h.case_id,),
    ).fetchone()
    assert row is not None
    return row[0], int(row[1])


@pytest.mark.parametrize("code", ["unavailable", "stopped"])
async def test_a_page_count_that_could_not_run_is_not_the_files_fault(
    h: Harness, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    script(monkeypatch, "count_pages", lambda n: code)
    _, doc = await lifecycle.upload_bytes(h, pdf(), filename="a.pdf")
    finished = await h.drain()
    assert document_state(h)[0] is None, "the document was marked after a failure that was not its doing"
    assert set(finished.values()) != {"done"}, "the job finished as if nothing had gone wrong"


@pytest.mark.parametrize("code", ["timed_out", "crashed"])
async def test_a_page_count_that_timed_out_or_crashed_is_tried_once_more_in_a_fresh_child(
    h: Harness, monkeypatch: pytest.MonkeyPatch, code: str
) -> None:
    seen = script(monkeypatch, "count_pages", lambda n: code if n == 0 else None)
    await lifecycle.run(h, pdf(), "a.pdf")
    assert document_state(h) == (None, 3), "a first failure alone marked the document"
    assert len(seen) == 2


async def test_a_page_count_that_fails_twice_marks_the_document_damaged_after_two_tries(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    seen = script(monkeypatch, "count_pages", lambda n: "timed_out")
    await lifecycle.run(h, pdf(), "a.pdf")
    assert document_state(h) == ("damaged", 0)
    assert len(seen) == 2, "not exactly one more try"


@pytest.mark.parametrize("step", ["cut_pages", "pictures"])
async def test_a_page_whose_cut_or_picture_could_not_run_is_not_listed_as_lost(
    h: Harness, monkeypatch: pytest.MonkeyPatch, step: str
) -> None:
    script(monkeypatch, step, lambda n: "unavailable")
    await lifecycle.upload_bytes(h, pdf(), filename="a.pdf")
    finished = await h.drain()
    assert all(not p["unreadable"] for p in h.pages()), "a page was listed unreadable by a failure not its own"
    assert set(finished.values()) != {"done"}


async def test_a_text_layer_check_that_could_not_run_does_not_make_the_page_unreadable(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    script(monkeypatch, "text_layer", lambda n: "unavailable")
    await lifecycle.upload_bytes(h, pdf(), filename="a.pdf")
    finished = await h.drain()
    assert all(not p["unreadable"] for p in h.pages())
    assert set(finished.values()) != {"done"}


async def test_the_split_route_says_unavailable_not_invalid_when_no_child_could_run_it(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    await lifecycle.run(h, pdf(2), "a.pdf")
    script(monkeypatch, "halves", lambda n: "unavailable")
    page = h.pages()[1]
    got = await h.client.post(
        f"/v1/cases/{h.case_id}/pages/{page['id']}/split", json={"axis": "vertical", "at": 0.5, "first": "left"}
    )
    assert got.status_code == 503 and got.json()["error"]["code"] == "unavailable", got.text


def test_the_page_count_timeout_grows_with_the_file_within_a_bound() -> None:
    base = 120.0
    small = parser_calls.count_timeout(1024, base)
    big = parser_calls.count_timeout(300 * 1024 * 1024, base)
    huge = parser_calls.count_timeout(50 * 1024**3, base)
    assert small == base, "a small file must not wait longer than the cell's timeout"
    assert base < big <= huge, "a big file gets no more time than a small one"
    assert huge <= 4 * base, "the timeout has no bound"
