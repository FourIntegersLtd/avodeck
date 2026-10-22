"""Each shipped specialty says which of its lane entries form a bar (lanes.yaml `series`); every field it matches
on is a field of the fact kinds it names. Read from the packs, never written here (rule 11)."""

from __future__ import annotations

import pytest

from app.core.packs import default_library
from tests.support.packs import BOTH, LAUNCH, Combo


def _series_lanes(combo: Combo) -> list[dict[str, object]]:
    return [lane for lane in default_library().combine(*combo.codes).lanes() if lane.get("series")]


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.specialty)
def test_each_specialty_names_a_series_on_a_lane(combo: Combo) -> None:
    assert _series_lanes(combo)


def test_the_launch_prescriptions_lane_groups_issues_of_the_same_medicine_and_dose() -> None:
    lane = next(lane for lane in default_library().combine(*LAUNCH.codes).lanes() if lane["key"] == "prescriptions")
    assert lane["series"] == {"kinds": ["prescription"], "same": ["drug", "dose"], "max_gap_days": 120, "min_count": 2}


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.specialty)
def test_the_series_kinds_are_on_the_lane_and_the_matched_fields_belong_to_them(combo: Combo) -> None:
    pack = default_library().combine(*combo.codes)
    kinds = {k["key"]: set(k.get("fields", {})) for k in pack.fact_kinds()}
    for lane in _series_lanes(combo):
        rule = lane["series"]
        assert isinstance(rule, dict)
        assert set(rule["kinds"]) <= set(lane["kinds"])  # type: ignore[arg-type]
        for kind in rule["kinds"]:
            assert set(rule["same"]) <= kinds[kind]


def test_the_two_specialties_use_different_kinds() -> None:
    kinds = [{tuple(lane["series"]["kinds"]) for lane in _series_lanes(c)} for c in BOTH]  # type: ignore[index]
    assert kinds[0].isdisjoint(kinds[1])
