"""Deadlines from pack rules (architecture 3.4, 3.5): calendar rule, working week, public holidays, timezones.

Timestamps are UTC; a deadline is worked out in the court's timezone and shown in the expert's.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.core import packs
from tests.support.packs import FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT


@pytest.fixture(scope="module")
def launch() -> packs.MergedPack:
    return packs.load_library(LAUNCH_ROOT).combine(*LAUNCH.codes)


@pytest.fixture(scope="module")
def fixture() -> packs.MergedPack:
    return packs.load_library(FIXTURE_ROOT).combine(*FIXTURE.codes)


def test_written_questions_are_due_28_days_after_service_under_the_launch_pack(launch: packs.MergedPack) -> None:
    deadline = launch.deadline("written_questions", date(2026, 10, 1))
    assert deadline.due_on == date(2026, 10, 1) + timedelta(days=28)
    assert deadline.kind == "written_questions"
    assert deadline.calendar == "calendar_days"
    assert deadline.rule_ref == LAUNCH.raw("jurisdiction", "deadlines.yaml")["deadlines"][0]["rule_ref"]


def test_a_deadline_landing_on_a_bank_holiday_moves_to_the_next_working_day(launch: packs.MergedPack) -> None:
    # 28 days after 3 Aug 2026 is Monday 31 Aug 2026, the summer bank holiday in England and Wales.
    assert launch.deadline("written_questions", date(2026, 8, 3)).due_on == date(2026, 9, 1)
    # 28 days after 27 Nov 2026 is Christmas Day; Boxing Day falls on a Saturday, so Monday 28 Dec is the
    # substitute bank holiday: the first working day is Tuesday 29 Dec.
    assert launch.deadline("written_questions", date(2026, 11, 27)).due_on == date(2026, 12, 29)


def test_a_deadline_landing_on_a_weekend_moves_using_the_packs_working_week(launch: packs.MergedPack) -> None:
    # 28 days after Saturday 3 Oct 2026 is Saturday 31 Oct.
    assert launch.deadline("written_questions", date(2026, 10, 3)).due_on == date(2026, 11, 2)


def test_business_day_deadlines_skip_the_fixture_packs_own_weekend_and_holidays(fixture: packs.MergedPack) -> None:
    # zz-test: 21 business days, working week Sunday to Thursday, holiday 24 Dec 2026.
    assert fixture.deadline("written_questions", date(2026, 10, 1)).due_on == date(2026, 11, 1)  # a Sunday
    assert fixture.deadline("written_questions", date(2026, 12, 1)).due_on == date(2026, 12, 31)
    assert fixture.deadline("written_questions", date(2026, 12, 1)).calendar == "business_days"


def test_the_same_service_date_gives_different_deadlines_under_each_pack(
    launch: packs.MergedPack, fixture: packs.MergedPack
) -> None:
    served = date(2026, 12, 1)
    assert launch.deadline("written_questions", served).due_on != fixture.deadline("written_questions", served).due_on


def test_a_service_time_is_read_as_a_date_in_the_courts_timezone(
    launch: packs.MergedPack, fixture: packs.MergedPack
) -> None:
    # 23:30 UTC on 30 Sep is already 1 Oct in the launch court's timezone ...
    late = datetime(2026, 9, 30, 23, 30, tzinfo=UTC)
    assert (
        launch.deadline("written_questions", late).due_on
        == launch.deadline("written_questions", date(2026, 10, 1)).due_on
    )
    # ... and 02:00 UTC on 2 Oct is still 1 Oct in the fixture court's timezone.
    early = datetime(2026, 10, 2, 2, 0, tzinfo=UTC)
    assert fixture.deadline("written_questions", early).due_on == date(2026, 11, 1)


def test_a_naive_service_time_is_refused(launch: packs.MergedPack) -> None:
    with pytest.raises(ValueError, match="timezone"):
        launch.deadline("written_questions", datetime(2026, 10, 1, 12, 0))


def test_a_deadline_is_stored_as_utc_and_shown_in_the_experts_timezone(launch: packs.MergedPack) -> None:
    deadline = launch.deadline("written_questions", date(2026, 10, 1))
    court = ZoneInfo(LAUNCH.raw("jurisdiction", "pack.yaml")["timezone"])
    assert deadline.due_at.utcoffset() == timedelta(0)
    assert deadline.due_at.astimezone(court).date() == deadline.due_on  # the end of the due day in court
    assert deadline.due_at.astimezone(court).time().hour == 23
    shown = deadline.shown_in("Pacific/Auckland")
    assert shown.tzinfo == ZoneInfo("Pacific/Auckland")
    assert shown == deadline.due_at
    assert shown.date() == deadline.due_on + timedelta(days=1)  # already the next morning there


def test_a_rule_set_by_court_direction_has_no_computed_date(launch: packs.MergedPack) -> None:
    manual = [r["key"] for r in launch.deadline_rules() if r.get("set_by") == "direction"]
    assert manual
    with pytest.raises(packs.DeadlineError, match="direction"):
        launch.deadline(manual[0], date(2026, 10, 1))


def test_an_unknown_deadline_kind_is_refused(fixture: packs.MergedPack) -> None:
    with pytest.raises(packs.DeadlineError, match="no_such_deadline"):
        fixture.deadline("no_such_deadline", date(2026, 10, 1))


def test_a_deadline_outside_the_packs_holiday_calendar_fails_closed(fixture: packs.MergedPack) -> None:
    covers_until = date.fromisoformat(FIXTURE.raw("jurisdiction", "holidays.yaml")["covers_until"])
    with pytest.raises(packs.DeadlineError, match="holiday"):
        fixture.deadline("written_questions", covers_until)
