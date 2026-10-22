"""Series bars on a lane (timeline-v2-followups item 5): the lane's pack rule groups entries of the named kinds
that agree on the named fields and are issued within the allowed gap. Rule words are read from the pack (rule 11)."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import pytest

from app.core.packs import MergedPack, default_library
from app.features.understand import repo, series
from tests.support.packs import BOTH, Combo

START = date(2024, 1, 1)


def _lane(pack: MergedPack) -> dict[str, Any]:
    return next(lane for lane in pack.lanes() if lane.get("series"))


class Rig:
    def __init__(self, combo: Combo) -> None:
        self.pack = default_library().combine(*combo.codes)
        self.lane = _lane(self.pack)
        self.rule = self.lane["series"]
        self.kind = self.rule["kinds"][0]
        self.same: list[str] = self.rule["same"]
        self.n = 0

    def fact(self, days: int, *, words: str = "Zed", last: str | None = None, **over: Any) -> repo.SeenFact:
        """A fact `days` after the start whose matched fields all read `words` (the last may differ)."""
        fields: dict[str, Any] = {name: words for name in self.same}
        if last is not None:
            fields[self.same[-1]] = last
        fields.update(over)
        self.n += 1
        return repo.SeenFact(
            fact_id=uuid.uuid4(), page_id=uuid.uuid4(), page_no=self.n, kind=self.kind, fields=fields, value="zz",
            event_date=START + timedelta(days=days), available_at=None, status="verified", version=1,
        )  # fmt: skip

    def build(self, *facts: repo.SeenFact) -> series.LaneSeries:
        return series.build(self.pack, self.lane["key"], list(facts))


@pytest.fixture(params=BOTH, ids=lambda c: c.specialty)
def rig(request: pytest.FixtureRequest) -> Rig:
    return Rig(request.param)


def test_issues_that_agree_form_one_bar_with_its_dates_count_and_keys(rig: Rig) -> None:
    a, b, c = rig.fact(0), rig.fact(20), rig.fact(40)
    got = rig.build(c, a, b)
    assert len(got.bars) == 1
    bar = got.bars[0]
    assert (bar.first, bar.last, bar.count) == (a.event_date, c.event_date, 3)
    assert bar.fact_ids == [a.fact_id, b.fact_id, c.fact_id]
    assert got.keys == {f.fact_id: bar.key for f in (a, b, c)}


def test_the_label_is_the_first_issue_s_own_words_for_the_matched_fields(rig: Rig) -> None:
    got = rig.build(rig.fact(0, words="Ab  Cd"), rig.fact(5, words="ab cd"))
    assert got.bars[0].label == " ".join(["Ab  Cd"] * len(rig.same))


def test_case_and_spacing_do_not_split_a_bar(rig: Rig) -> None:
    got = rig.build(rig.fact(0, words="Ab Cd"), rig.fact(5, words="  ab   CD "))
    assert [b.count for b in got.bars] == [2]


def test_a_change_in_a_matched_field_starts_a_new_bar(rig: Rig) -> None:
    facts = [rig.fact(0), rig.fact(10), rig.fact(20, last="Other"), rig.fact(30, last="Other")]
    got = rig.build(*facts)
    assert [b.count for b in got.bars] == [2, 2]
    assert got.bars[0].key != got.bars[1].key


def test_a_pause_longer_than_the_allowed_gap_ends_a_bar(rig: Rig) -> None:
    gap = rig.rule["max_gap_days"]
    got = rig.build(rig.fact(0), rig.fact(10), rig.fact(10 + gap + 1), rig.fact(20 + gap + 1))
    assert [b.count for b in got.bars] == [2, 2]
    held = rig.build(rig.fact(0), rig.fact(gap))
    assert [b.count for b in held.bars] == [2]


def test_fewer_than_the_minimum_make_no_bar(rig: Rig) -> None:
    short = rig.build(*[rig.fact(i * 10) for i in range(rig.rule["min_count"] - 1)])
    assert short.bars == [] and short.keys == {}


def test_facts_of_other_kinds_or_missing_a_field_or_a_date_are_left_out(rig: Rig) -> None:
    bare = rig.fact(5)
    del bare.fields[rig.same[0]]
    empty = rig.fact(6, **{rig.same[0]: "  "})
    undated = rig.fact(7)
    undated.event_date = None
    other = rig.fact(8)
    other.kind = "zz_other"
    ok = [rig.fact(0), rig.fact(9)]
    got = rig.build(bare, empty, undated, other, *ok)
    assert got.bars[0].fact_ids == [f.fact_id for f in ok]
    assert all(f.fact_id not in got.keys for f in (bare, empty, undated, other))


def test_bars_are_in_order_of_first_date_and_keys_are_unique(rig: Rig) -> None:
    late = [rig.fact(200, words="Late"), rig.fact(210, words="Late")]
    early = [rig.fact(0), rig.fact(10)]
    got = rig.build(*late, *early)
    assert [b.first for b in got.bars] == sorted(b.first for b in got.bars)
    assert len({b.key for b in got.bars}) == 2


def test_a_lane_without_a_rule_has_no_bars(rig: Rig) -> None:
    plain = next(lane for lane in rig.pack.lanes() if not lane.get("series"))
    got = series.build(rig.pack, plain["key"], [rig.fact(0), rig.fact(5)])
    assert got.bars == [] and got.keys == {}
    assert series.build(rig.pack, "zz_no_such_lane", [rig.fact(0), rig.fact(5)]).bars == []
