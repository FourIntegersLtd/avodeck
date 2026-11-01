"""The rest of what the S11 and S12 mock-ups need from the server.

- Timeline: the record's date range (before any filter), "as the clinician saw it on a day" (only entries that had
  arrived by then; ones with no known arrival are left out), two more filters (not linked to a question, things
  to look at) and a colour for each line (the pack's colour of the line's main source).
- `GET /consultations/{fact_id}/later-notes`: later entries of the same kind that arrived after the day, listed so
  the expert knows they exist; their words are not sent. Says whether you already keep a note on each.
"""

from __future__ import annotations

from datetime import date

import pytest

from tests.http.test_understand_routes_timeline import Case, _plain_lanes
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def _events(body: dict[str, object]) -> list[dict[str, object]]:
    return [e for lane in body["lanes"] for e in lane["events"]]  # type: ignore[attr-defined]


async def test_the_timeline_gives_the_date_range_of_the_whole_record(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    case.fact(kind, date(2024, 1, 10))
    case.fact(kind, date(2024, 5, 10))
    case.fact(kind, None)
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline", params={"from": "2024-04-01"})
    assert resp.json()["range"] == {"first": "2024-01-10", "last": "2024-05-10"}


async def test_the_timeline_can_show_only_what_had_arrived_by_a_day(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    early = case.fact(kind, date(2024, 1, 10), available=date(2024, 1, 11))
    case.fact(kind, date(2024, 1, 20), available=date(2024, 3, 1))
    case.fact(kind, date(2024, 1, 25))  # arrival not known
    expert = await client_for(case.expert)
    body = (await expert.get(f"/v1/cases/{case.id}/timeline", params={"as_seen_on": "2024-02-01"})).json()
    assert [e["fact_id"] for e in _events(body)] == [str(early)]
    assert body["as_seen_on"] == "2024-02-01"
    assert body["as_seen_title"] == case.pack.term("as_seen_title"), "the label for the date comes from the pack"
    assert body["counts"]["total"] == 3, "the chip numbers ignore the filters"
    assert (await expert.get(f"/v1/cases/{case.id}/timeline")).json()["as_seen_on"] is None


async def test_the_timeline_can_show_only_what_is_not_linked_or_has_a_thing_to_look_at(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    kind = _plain_lanes(case.pack)[0]["kinds"][0]
    linked = case.fact(kind, date(2024, 1, 10))
    loose = case.fact(kind, date(2024, 2, 10))
    case.link(linked, case.question())
    case.admin.execute(
        "insert into app.findings (case_id, check_key, rule, gentle, evidence_fact_ids, evidence_page_ids,"
        " pack_version)"
        " select %s, 'zz', 'zz', false, %s, array[page_id], 'zz@1' from app.facts where id = %s",
        (case.id, [loose], loose),
    )
    expert = await client_for(case.expert)
    got = (await expert.get(f"/v1/cases/{case.id}/timeline", params={"only": "not_linked"})).json()
    assert [e["fact_id"] for e in _events(got)] == [str(loose)]
    got = (await expert.get(f"/v1/cases/{case.id}/timeline", params={"only": "things_to_look"})).json()
    assert [e["fact_id"] for e in _events(got)] == [str(loose)]


async def test_each_line_has_the_colour_of_its_main_source(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    lanes = _plain_lanes(case.pack)
    case.fact(lanes[0]["kinds"][0], date(2024, 1, 10))
    source = case.pack.source_types()[0]
    case.admin.execute(
        "update app.pages set source_type = %s where bundle_id = (select bundle_id from app.cases where id = %s)",
        (source["key"], case.id),
    )
    body = (await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")).json()
    colours = {lane["key"]: lane["colour"] for lane in body["lanes"]}
    assert colours[lanes[0]["key"]] == source["colour"]
    assert all(c is None for k, c in colours.items() if k != lanes[0]["key"])


async def test_later_notes_list_later_entries_of_the_same_kind_without_their_words(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    lanes = _plain_lanes(case.pack)
    kind, other = lanes[0]["kinds"][0], lanes[1]["kinds"][0]
    visit = case.fact(kind, date(2024, 5, 15), available=date(2024, 5, 15))
    case.fact(kind, date(2024, 5, 1), available=date(2024, 5, 2))  # arrived before: not "later"
    later = case.fact(kind, date(2024, 8, 8), available=date(2024, 8, 9), page=2)
    case.fact(other, date(2024, 8, 8), available=date(2024, 8, 9), page=3)  # another kind
    expert = await client_for(case.expert)
    got = (await expert.get(f"/v1/cases/{case.id}/consultations/{visit}/later-notes")).json()
    [note] = got["notes"]
    assert note["fact_id"] == str(later) and note["page_no"] == 2 and note["event_date"] == "2024-08-08"
    assert note["has_note"] is False and "summary" not in note
    await expert.put(f"/v1/cases/{case.id}/consultations/{later}/note", json={"body": "mine"})
    got = (await expert.get(f"/v1/cases/{case.id}/consultations/{visit}/later-notes")).json()
    assert got["notes"][0]["has_note"] is True
    assert (
        await (await client_for(case.stranger)).get(f"/v1/cases/{case.id}/consultations/{visit}/later-notes")
    ).status_code == 404
