"""Slice A: the sorted view (brief acceptance 1: grouped by source then date, never renumbered).

`GET /v1/cases/{id}/bundle/sorted` lists the case's pages in the pack's source order, then by the
page's date (the earliest date printed on the page, page-level data only), pages with no date after
dated ones, then by page number. Nothing comes from facts: a page not yet read must not reveal a
suggestion, even its date (rule 3). Each entry keeps the page's own
`page_no`: sorting never changes a number. Pages set aside as not this person's are left out.
A viewer may look (case.view_content); anyone without access gets 404. Runs under both packs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.http.test_pipeline_duplicates_routes import member, stranger
from tests.repository.test_pipeline_support import Harness, harness
from tests.support.contracts import add_fact
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture(params=BOTH, ids=lambda c: c.codes[0])
async def h(
    request: pytest.FixtureRequest,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    combo: Combo = request.param
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
    ) as running:
        running.sources = [s["key"] for s in default_library().combine(*combo.codes).source_types()]  # type: ignore[attr-defined]
        yield running


def page(h: Harness, doc: uuid.UUID, n: int, source: str | None, printed: list[date] | None = None) -> uuid.UUID:
    pid = Seed(h.admin).page(h.case_id, doc, n)
    h.admin.execute("update app.pages set source_type = %s where id = %s", (source, pid))
    h.admin.execute(
        "insert into app.page_analysis (page_id, case_id, printed_dates) values (%s, %s, %s)",
        (pid, h.case_id, printed or []),
    )
    return pid


def dated_fact(h: Harness, pid: uuid.UUID, on: date, status: str = "suggested") -> None:
    fid = add_fact(h.admin, h.case_id, pid, status=status)
    h.admin.execute("update app.facts set event_date = %s where id = %s", (on, fid))


def arrange(h: Harness) -> None:
    first, second = h.sources[0], h.sources[1]  # type: ignore[attr-defined]
    doc = Seed(h.admin).document(h.case_id)
    one = page(h, doc, 1, second)
    dated_fact(h, one, date(2021, 5, 1))
    page(h, doc, 2, first, [date(2023, 1, 1)])
    three = page(h, doc, 3, first, [date(2024, 6, 6)])
    dated_fact(h, three, date(2020, 2, 2))
    dated_fact(h, three, date(2019, 1, 1), status="rejected")
    page(h, doc, 4, None)
    page(h, doc, 5, second)
    six = page(h, doc, 6, first, [date(2018, 1, 1)])
    h.admin.execute("update app.pages set quarantined = true where id = %s", (six,))


async def test_pages_are_grouped_by_source_then_date_and_keep_their_numbers(h: Harness) -> None:
    arrange(h)
    before = h.admin.execute(
        "select id, bundle_page_no from app.pages where case_id = %s order by 1", (h.case_id,)
    ).fetchall()
    got = await h.client.get(f"/v1/cases/{h.case_id}/bundle/sorted")
    assert got.status_code == 200, got.status_code
    assert got.headers["cache-control"] == "private, no-store"
    first, second = h.sources[0], h.sources[1]  # type: ignore[attr-defined]
    assert [(p["page_no"], p["source_type"], p["date"]) for p in got.json()["pages"]] == [
        (2, first, "2023-01-01"),
        (3, first, "2024-06-06"),
        (1, second, None),
        (5, second, None),
        (4, None, None),
    ]
    after = h.admin.execute(
        "select id, bundle_page_no from app.pages where case_id = %s order by 1", (h.case_id,)
    ).fetchall()
    assert after == before, "sorting changed a page number"


async def test_a_viewer_may_look_and_a_stranger_gets_404(h: Harness) -> None:
    arrange(h)
    path = f"/v1/cases/{h.case_id}/bundle/sorted"
    assert (await h.client.get(path, headers=member(h, "viewer"))).status_code == 200
    assert (await h.client.get(path, headers=stranger(h))).status_code == 404


def test_the_sorted_view_never_reads_facts() -> None:
    """Rule 3: the sort uses page-level data only, so no facts query runs for pages not yet read."""
    import inspect

    from app.features.pipeline import repo, router

    assert "facts" not in inspect.getsource(repo.sorted_pages)
    assert "facts" not in inspect.getsource(router.bundle_sorted)
