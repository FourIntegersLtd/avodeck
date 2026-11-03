"""Slice A: the bundle summary carries the retry run's progress (`retry`), so the browser only shows numbers
the server counted. Absent when no retry has run lately. Counts only (rule 6)."""

# ruff: noqa: F811 (the `h` fixture is imported, then named by each test)
from __future__ import annotations

import uuid

import pytest

from tests.http.test_pipeline_retry_routes import h, storage  # noqa: F401
from tests.repository.test_pipeline_support import Harness
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def pages(h: Harness, spec: list[tuple[str | None, str | None]], run: uuid.UUID | None = None, first: int = 1) -> None:
    """Pages with words; each spec is (read, failed): how long ago its entries were read / failed. `run` is the
    retry job that took them."""
    seed = Seed(h.admin)
    doc = seed.document(h.case_id)
    for n, (read, failed) in enumerate(spec, start=first):
        p = seed.page(h.case_id, doc, n)
        h.admin.execute("update app.pages set text_source = 'ocr' where id = %s", (p,))
        h.admin.execute(
            "insert into app.page_analysis (page_id, bundle_id, stage, extracted_at, entries_extracted_at,"
            " entries_failed_at, retry_job_id) values (%s, (select bundle_id from app.cases where id = %s), 'extract',"
            " now(), now() - %s::interval, now() - %s::interval, %s)",
            (p, h.case_id, read, failed, run),
        )


def job(h: Harness, *statuses: str) -> uuid.UUID:
    """A retry job queued ten minutes ago, then moved through `statuses`."""
    row = h.admin.execute(
        "insert into app.jobs (case_id, kind, created_at) values (%s, 'retry_unread_pages',"
        " now() - interval '10 minutes') returning id",
        (h.case_id,),
    ).fetchone()
    assert row is not None
    for status in statuses:
        h.admin.execute(
            "update app.jobs set status = %s, finished_at = case when %s = 'done' then now() end where id = %s",
            (status, status, row[0]),
        )
    return row[0]  # type: ignore[no-any-return]


async def test_the_summary_has_no_retry_before_one_has_run(h: Harness) -> None:
    pages(h, [(None, "1 hour")])
    got = (await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")).json()
    assert got["retry"] is None


async def test_the_summary_counts_a_running_retry_and_only_its_own_pages(h: Harness) -> None:
    run = job(h, "running")
    pages(h, [("1 minute", None), (None, "1 minute"), (None, "1 hour"), (None, "1 hour")], run)
    pages(h, [("1 minute", None)], first=50)  # a page read while the run went on, which it did not take
    got = (await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")).json()
    assert got["retry"] == {
        "running": True,
        "paused": False,
        "total": 4,
        "done": 1,
        "failed": 1,
        "pause_reason": None,
        "allowance_used_up": False,
    }
    assert got["retrying"] is True and got["failed_pages"] == 3


async def test_a_paused_retry_says_so_rather_than_running(h: Harness) -> None:
    run = job(h, "running", "paused")
    pages(h, [("1 minute", None), (None, "1 hour")], run)
    got = (await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")).json()
    assert got["retry"] == {
        "running": False,
        "paused": True,
        "total": 2,
        "done": 1,
        "failed": 0,
        "pause_reason": "other",
        "allowance_used_up": False,
    }


async def test_a_finished_retry_reports_how_it_ended(h: Harness) -> None:
    run = job(h, "running", "done")
    pages(h, [("1 minute", None), (None, "1 minute")], run)
    got = (await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")).json()
    assert got["retry"] == {
        "running": False,
        "paused": False,
        "total": 2,
        "done": 1,
        "failed": 1,
        "pause_reason": None,
        "allowance_used_up": False,
    }
    assert got["retrying"] is False
