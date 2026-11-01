# ruff: noqa: F811
"""A purged quarantine leaves no extracted value and, once none of its pages remain, no source file."""

from __future__ import annotations

import datetime as dt

import pytest

from tests.repository.test_pipeline_quarantine import OTHER_PAGE, page_id, quarantine_path
from tests.repository.test_pipeline_quarantine_purge import (  # noqa: F401
    confirmed_on,
    h,
    purge,
    storage,
)
from tests.repository.test_pipeline_support import Harness

pytestmark = pytest.mark.anyio


def add_fact(h: Harness, n: int) -> None:
    h.admin.execute(
        "insert into app.facts (case_id, page_id, kind, fields, value, word_ids)"
        " values (%s, %s, 'synthetic_kind', '{\"k\": \"synthetic\"}', 'synthetic value', array['w1'])",
        (h.case_id, page_id(h, n)),
    )


def facts_of(h: Harness, n: int) -> list[tuple[object, object, str]]:
    return [
        tuple(r)
        for r in h.admin.execute(
            "select value, fields, status from app.facts where page_id = %s", (page_id(h, n),)
        ).fetchall()
    ]


def source_path(h: Harness) -> str | None:
    row = h.admin.execute("select storage_path from app.documents where bundle_id = (select bundle_id from app.cases where id = %s)", (h.case_id,)).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


async def test_the_values_read_from_a_purged_page_are_gone(h: Harness) -> None:
    add_fact(h, OTHER_PAGE)
    add_fact(h, 1)
    await purge(h, confirmed_on(h) + dt.timedelta(days=h.days))  # type: ignore[attr-defined]
    gone = facts_of(h, OTHER_PAGE)
    assert len(gone) >= 2 and all(f == (None, {}, "rejected") for f in gone)
    assert ("synthetic value", {"k": "synthetic"}, "suggested") in facts_of(h, 1)


async def test_the_source_file_goes_only_when_every_page_of_it_is_purged(h: Harness) -> None:
    days: int = h.days  # type: ignore[attr-defined]
    path = source_path(h)
    assert path is not None
    await purge(h, confirmed_on(h) + dt.timedelta(days=days))
    assert source_path(h) == path, "the file was deleted while other pages of it were still in use"
    assert await h.storage.list_recursive(path)

    for n in (1, 2, 3, 5):
        assert (await h.client.post(quarantine_path(h, n), json={"reason": "not_this_person"})).status_code == 200
    await purge(h, confirmed_on(h) + dt.timedelta(days=days + 30))
    assert source_path(h) is None
    assert await h.storage.list_recursive(path) == [], "the uploaded file was kept"
