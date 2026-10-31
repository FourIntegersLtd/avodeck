"""Slice G, screens 19 and 20: the days left to a court date, counted by the server in the court's own timezone."""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from app.features.after_serving.common import days_left
from app.features.after_serving.routes_deadlines import deadline_out


def test_days_are_counted_in_the_courts_timezone_not_utc() -> None:
    # 23:30 UTC on 1 July is already 2 July in Pacific/Auckland, so a deadline on 3 July has 1 day left there.
    now = datetime(2026, 7, 1, 23, 30, tzinfo=UTC)
    assert days_left(date(2026, 7, 3), "Pacific/Auckland", now) == 1
    assert days_left(date(2026, 7, 3), "UTC", now) == 2


def test_the_day_itself_is_zero_and_a_missed_date_is_negative() -> None:
    now = datetime(2026, 7, 3, 9, 0, tzinfo=UTC)
    assert days_left(date(2026, 7, 3), "Europe/London", now) == 0
    assert days_left(date(2026, 7, 1), "Europe/London", now) == -2


def test_a_deadline_carries_its_days_left() -> None:
    row = {
        "id": uuid.uuid4(),
        "kind": "written_questions",
        "due_on": date(2099, 1, 1),
        "rule_ref": None,
        "calendar": None,
        "timezone": "Europe/London",
        "created_from": "pack",
        "source": "pack",
        "pack_version": None,
        "hearing_id": None,
        "done_at": None,
        "cancelled_at": None,
        "last_reminded_on": None,
        "version": 1,
    }
    days_left = deadline_out(row).days_left
    assert days_left is not None and days_left > 0
