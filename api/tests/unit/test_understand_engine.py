"""Slice D's code-only engine: lanes, availability and "as seen" (docs/slices/D-understand.md).

- A fact goes in the first lane whose `when` matches, else the first lane listing its kind; the lane
  list and its order are the pack's.
- `available_at` is the earliest availability mark that belongs to the fact (a mark belongs to the
  run of arriving items just before it on the page, or just after it when none comes before; the
  clinician's own entries break runs). Own-record entries count from their own date; anything that
  arrives with no mark is unknown. Never the date of a letter. The latest override wins.
- As seen on a date: visible = available on or before that date; later ones are counted apart;
  unknown ones are listed as unknown, never placed before the date. Rejected facts are left out.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from app.features.understand.as_seen import SeenItem, as_seen
from app.features.understand.availability import Mark, Override, Placed, compute
from app.features.understand.lanes import arrange, lane_for

LANES: list[Any] = [
    {"key": "visits", "kinds": ["consultation", "phone_consultation"]},
    {"key": "after_hours", "kinds": ["consultation", "phone_consultation"], "when": {"setting": "out_of_hours"}},
    {"key": "tests", "kinds": ["result"]},
    {"key": "post", "kinds": ["letter", "referral"]},
]


def _id() -> uuid.UUID:
    return uuid.uuid4()


# --- lanes ---------------------------------------------------------------------------------------------


def test_a_fact_goes_in_the_first_lane_listing_its_kind() -> None:
    assert lane_for("result", {}, LANES) == "tests"
    assert lane_for("consultation", {"setting": "surgery"}, LANES) == "visits"


def test_a_lane_whose_condition_matches_wins_over_an_earlier_lane() -> None:
    assert lane_for("consultation", {"setting": "out_of_hours"}, LANES) == "after_hours"


def test_a_kind_no_lane_lists_is_unplaced() -> None:
    assert lane_for("prescription", {}, LANES) is None


def test_plain_string_lanes_have_no_kinds() -> None:
    assert lane_for("result", {}, [{"key": "tests"}]) is None


def test_arrange_keeps_the_pack_order_and_every_fact() -> None:
    facts: list[tuple[str, str, dict[str, Any]]] = [
        ("a", "letter", {}),
        ("b", "result", {}),
        ("c", "prescription", {}),
        ("d", "consultation", {}),
    ]
    lanes, unplaced = arrange(facts, LANES, kind=lambda f: f[1], fields=lambda f: f[2])
    assert [lane for lane, _ in lanes] == ["visits", "after_hours", "tests", "post"]
    assert dict(lanes)["post"] == [facts[0]]
    assert dict(lanes)["after_hours"] == []
    assert unplaced == [facts[2]]


def test_arrange_follows_a_different_pack_without_code_change() -> None:
    other = [{"key": "measurements", "kinds": ["measurement"]}, {"key": "sessions", "kinds": ["session"]}]
    facts: list[tuple[str, str, dict[str, Any]]] = [("a", "session", {}), ("b", "measurement", {})]
    lanes, unplaced = arrange(facts, other, kind=lambda f: f[1], fields=lambda f: f[2])
    assert [lane for lane, _ in lanes] == ["measurements", "sessions"]
    assert unplaced == []


# --- availability --------------------------------------------------------------------------------------

ARRIVING = frozenset({"result", "letter"})
PAGE = _id()


def _placed(kind: str, position: int, event: date, own_record: bool = True, page: uuid.UUID = PAGE) -> Placed:
    return Placed(fact_id=_id(), page_id=page, position=position, kind=kind, event_date=event, own_record=own_record)


def test_a_filed_mark_dates_the_report_just_above_it_not_the_one_below() -> None:
    """Page 118: the 13 May results are filed 14 May; a later letter on the page has its own mark."""
    consult = _placed("consultation", 10, date(2024, 5, 15))
    result = _placed("result", 20, date(2024, 5, 13))
    letter = _placed("letter", 40, date(2024, 3, 31))
    marks = [Mark(PAGE, "filed", date(2024, 5, 14), 30), Mark(PAGE, "filed", date(2024, 4, 2), 50)]
    got = compute([consult, result, letter], marks, ARRIVING, [])
    assert got[result.fact_id].available_at == date(2024, 5, 14)
    assert got[result.fact_id].basis == "filed"
    assert got[letter.fact_id].available_at == date(2024, 4, 2)
    assert got[consult.fact_id].available_at == date(2024, 5, 15)
    assert got[consult.fact_id].basis == "own_record"


def test_a_stamp_at_the_top_dates_the_letter_below_it_and_the_earliest_mark_wins() -> None:
    """A hospital letter dated 10 May, stamped received 22 May at the top and seen 28 May at the foot."""
    page = _id()
    letter = _placed("letter", 20, date(2024, 5, 10), own_record=False, page=page)
    marks = [Mark(page, "received", date(2024, 5, 22), 5), Mark(page, "seen", date(2024, 5, 28), 90)]
    got = compute([letter], marks, ARRIVING, [])
    assert got[letter.fact_id].available_at == date(2024, 5, 22)
    assert got[letter.fact_id].basis == "received"


def test_a_letter_with_no_mark_is_unknown_never_its_own_date() -> None:
    letter = _placed("letter", 20, date(2024, 5, 10))
    got = compute([letter], [], ARRIVING, [])
    assert got[letter.fact_id].available_at is None
    assert got[letter.fact_id].basis is None


def test_anything_on_another_service_s_record_arrives_even_if_its_kind_does_not() -> None:
    """An out-of-hours consultation on the urgent-care record arrives when it is sent on."""
    page = _id()
    contact = _placed("consultation", 10, date(2024, 3, 30), own_record=False, page=page)
    unmarked = _placed("consultation", 10, date(2024, 3, 30), own_record=False)
    marks = [Mark(page, "sent_to_clinician", date(2024, 3, 31), 60)]
    got = compute([contact, unmarked], marks, ARRIVING, [])
    assert got[contact.fact_id].available_at == date(2024, 3, 31)
    assert got[unmarked.fact_id].available_at is None


def test_a_mark_with_an_unreadable_date_is_ignored() -> None:
    result = _placed("result", 20, date(2024, 5, 13))
    got = compute([result], [Mark(PAGE, "filed", None, 30)], ARRIVING, [])
    assert got[result.fact_id].available_at is None


def test_own_entries_break_runs() -> None:
    """A mark below the clinician's own entry does not reach back past it to an earlier result."""
    result = _placed("result", 10, date(2024, 5, 1))
    consult = _placed("consultation", 20, date(2024, 5, 3))
    marks = [Mark(PAGE, "filed", date(2024, 5, 9), 30)]
    got = compute([result, consult], marks, ARRIVING, [])
    assert got[result.fact_id].available_at is None


def test_the_latest_override_wins_including_unknown() -> None:
    letter = _placed("letter", 20, date(2024, 5, 10))
    early = datetime(2026, 1, 1, tzinfo=UTC)
    late = datetime(2026, 1, 2, tzinfo=UTC)
    overrides = [
        Override(letter.fact_id, None, True, late),
        Override(letter.fact_id, date(2024, 5, 12), False, early),
    ]
    marks = [Mark(PAGE, "filed", date(2024, 5, 20), 30)]
    got = compute([letter], marks, ARRIVING, overrides)
    assert got[letter.fact_id].available_at is None
    assert got[letter.fact_id].basis == "expert_unknown"
    got = compute([letter], marks, ARRIVING, overrides[1:])
    assert got[letter.fact_id].available_at == date(2024, 5, 12)
    assert got[letter.fact_id].basis == "expert_override"


# --- as seen -------------------------------------------------------------------------------------------


def test_as_seen_on_the_day_hides_what_came_later_and_lists_the_unknown() -> None:
    consult = SeenItem(_id(), date(2024, 5, 15), "verified")
    blood = SeenItem(_id(), date(2024, 5, 14), "verified")
    letter = SeenItem(_id(), date(2024, 5, 22), "verified")
    unknown = SeenItem(_id(), None, "suggested")
    rejected = SeenItem(_id(), date(2024, 5, 1), "rejected")
    view = as_seen(date(2024, 5, 15), [consult, blood, letter, unknown, rejected], exclude=consult.fact_id)
    assert view.visible == [blood]
    assert view.later == [letter]
    assert view.unknown == [unknown]


def test_something_available_on_the_day_itself_is_visible() -> None:
    same_day = SeenItem(_id(), date(2024, 5, 15), "edited")
    assert as_seen(date(2024, 5, 15), [same_day], exclude=None).visible == [same_day]
