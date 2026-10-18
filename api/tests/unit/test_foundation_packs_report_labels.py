"""Report section and appendix labels come from the case-type pack (rule 11), not from a humanised key.

Each entry in `report_sections.yaml` is `{key, label}`; the label is the heading the expert sees on
screens 16 and 17. The case pack endpoint serves both lists with their labels.
"""

from __future__ import annotations

import pytest

from app.core import packs
from app.core.routes.packs import case_pack
from tests.support.packs import BOTH, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_every_section_and_appendix_has_a_pack_label(library: packs.PackLibrary, combo: Combo) -> None:
    raw = combo.raw("case-type", "report_sections.yaml")
    pack = library.combine(*combo.codes)
    for merged, listed in ((pack.report_sections(), raw["sections"]), (pack.report_appendices(), raw["appendices"])):
        assert [item["key"] for item in merged] == [item["key"] for item in listed]
        assert [item["label"] for item in merged] == [item["label"] for item in listed]
        assert all(isinstance(item["label"], str) and item["label"].strip() for item in merged)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_case_pack_serves_sections_and_appendices_with_labels(library: packs.PackLibrary, combo: Combo) -> None:
    pack = library.combine(*combo.codes)
    served = case_pack(pack)
    assert served.report_sections == pack.report_sections()
    assert served.report_appendices == pack.report_appendices()
    assert all("label" in item for item in [*served.report_sections, *served.report_appendices])


def test_launch_labels_read_as_headings(library: packs.PackLibrary) -> None:
    pack = library.combine(*LAUNCH.codes)
    labels = {s["key"]: s["label"] for s in pack.report_sections()}
    assert labels["what_happened"] == "What happened"
    assert labels["range_of_opinion"] == "Range of opinion"
