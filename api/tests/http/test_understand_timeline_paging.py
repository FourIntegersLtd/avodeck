"""The timeline filtered and paged in SQL (scalability plan PR 11).

`limit` and `cursor` are optional and only grow the contract: with no `limit` the reply is the same as before
(and `next_cursor` is null). With a `limit`, the entries come in date order a page at a time, every entry once,
in the same order as the whole list. Counts and the range still cover the whole record.
"""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from tests.http.test_understand_routes_timeline import Case, _plain_lanes
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def _stock(case: Case) -> list[Any]:
    """Seven entries over two lanes: a tie on day and page, an entry with no day and a suggestion."""
    lanes = _plain_lanes(case.pack)
    first, last = lanes[0]["kinds"][0], lanes[-1]["kinds"][0]
    return [
        case.fact(first, date(2024, 3, 1), page=4),
        case.fact(last, date(2024, 1, 5), page=7),
        case.fact(first, date(2024, 3, 1), page=4),
        case.fact(first, date(2024, 3, 1), page=2),
        case.fact(last, None, page=9),
        case.fact(last, date(2024, 6, 9), page=1),
        case.fact(first, None, page=3),
    ]


async def _pages(client: Any, case: Case, limit: int, **params: Any) -> list[dict[str, Any]]:
    out, cursor = [], None
    for _ in range(50):
        query = {**params, "limit": limit, **({"cursor": cursor} if cursor else {})}
        resp = await client.get(f"/v1/cases/{case.id}/timeline", params=query)
        assert resp.status_code == 200, resp.text
        out.append(resp.json())
        cursor = resp.json()["next_cursor"]
        if cursor is None:
            return out
    raise AssertionError("the cursors never ended")


def _ids(body: dict[str, Any]) -> list[str]:
    return [r["fact_id"] for r in body["chronology"]]


def _lane_ids(body: dict[str, Any]) -> set[str]:
    return {e["fact_id"] for lane in body["lanes"] for e in lane["events"]} | {e["fact_id"] for e in body["unplaced"]}


async def test_a_limit_returns_at_most_that_many_entries_and_the_cursors_walk_every_entry_once_in_order(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    _stock(case)
    client = await client_for(case.expert)
    whole = (await client.get(f"/v1/cases/{case.id}/timeline")).json()
    assert whole["next_cursor"] is None
    assert len(_ids(whole)) == 7

    pages = await _pages(client, case, 3)
    assert [len(_ids(p)) for p in pages] == [3, 3, 1]
    assert all(len(_lane_ids(p)) == len(_ids(p)) for p in pages), "lanes hold the page's entries and no others"
    assert [i for p in pages for i in _ids(p)] == _ids(whole)
    assert set().union(*(_lane_ids(p) for p in pages)) == _lane_ids(whole)


async def test_a_limit_as_large_as_the_record_gives_the_whole_record_and_no_cursor(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    _stock(case)
    client = await client_for(case.expert)
    whole = (await client.get(f"/v1/cases/{case.id}/timeline")).json()
    body = (await client.get(f"/v1/cases/{case.id}/timeline", params={"limit": 7})).json()
    assert body["next_cursor"] is None
    assert _ids(body) == _ids(whole)


async def test_filters_still_apply_across_pages(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    ids = _stock(case)
    question = case.question()
    case.link(ids[0], question)
    case.link(ids[5], question)
    client = await client_for(case.expert)
    for params in (
        {"from": "2024-02-01", "to": "2024-12-31"},
        {"question_id": str(question)},
        {"only": "not_linked"},
        {"status": "verified"},
    ):
        whole = (await client.get(f"/v1/cases/{case.id}/timeline", params=params)).json()
        pages = await _pages(client, case, 2, **params)
        assert [i for p in pages for i in _ids(p)] == _ids(whole), params
        assert whole["chronology"], params


async def test_counts_and_range_cover_the_whole_record_whatever_the_page(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    _stock(case)
    client = await client_for(case.expert)
    whole = (await client.get(f"/v1/cases/{case.id}/timeline")).json()
    first = (await client.get(f"/v1/cases/{case.id}/timeline", params={"limit": 2})).json()
    assert first["counts"] == whole["counts"]
    assert first["range"] == whole["range"]
    assert first["status_options"] == whole["status_options"]
    assert first["next_cursor"]


async def test_suggestions_on_unread_pages_stay_hidden_when_paged(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    shown = case.fact(kind, date(2024, 1, 1), page=1)
    hidden = case.fact(kind, date(2024, 1, 2), page=2, status="suggested")
    read = case.fact(kind, date(2024, 1, 3), page=3, status="suggested")
    case.admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 20)",
        (case.id, case.page(3), case.expert),
    )
    pages = await _pages(await client_for(case.expert), case, 1)
    assert [i for p in pages for i in _ids(p)] == [str(shown), str(read)]
    assert str(hidden) not in {i for p in pages for i in _ids(p)}


async def test_a_limit_over_500_or_a_made_up_cursor_is_refused(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    _stock(case)
    client = await client_for(case.expert)
    url = f"/v1/cases/{case.id}/timeline"
    assert (await client.get(url, params={"limit": 501})).status_code == 422
    assert (await client.get(url, params={"limit": 0})).status_code == 422
    assert (await client.get(url, params={"limit": 2, "cursor": "not-a-cursor"})).status_code == 422
    assert (await client.get(url, params={"limit": 500})).status_code == 200
