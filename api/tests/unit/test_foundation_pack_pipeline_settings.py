"""Pack settings the bundle pipeline needs (slice A): the instructing-party request and quarantine.

- `instructing_party_request(**slots)`: the label, subject and body of the email draft asking the
  instructing party for missing records (screen 08b), from the jurisdiction's vocabulary. Slots
  the caller fills (`case_reference`, `missing_items`, `clearer_copies`, `expert_name`) and pack
  words (`{instructing_party}`) are filled; a template slot with no value is refused.
- `quarantine_purge_days`: how long a file that failed the virus scan is kept before it is purged.
"""

from __future__ import annotations

import pytest

from app.core import packs
from tests.support.packs import BOTH, FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo

SLOTS = {
    "case_reference": "REF-0001",
    "missing_items": "- item one\n- item two",
    "clearer_copies": "- page 12",
    "expert_name": "Dr A Expert",
}


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_the_request_wording_comes_from_the_jurisdiction_vocabulary(library: packs.PackLibrary, combo: Combo) -> None:
    pack = library.combine(*combo.codes)
    raw = combo.raw("jurisdiction", "vocabulary.yaml")["instructing_party_request"]
    request = pack.instructing_party_request(**SLOTS)
    assert set(request) == {"label", "subject", "body"}
    assert request["label"] == raw["label"]
    assert SLOTS["case_reference"] in request["subject"]
    for value in (SLOTS["missing_items"], SLOTS["clearer_copies"], SLOTS["expert_name"]):
        assert value in request["body"]
    assert "{" not in request["subject"] + request["body"], "every slot is filled"


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_a_missing_slot_is_refused(library: packs.PackLibrary, combo: Combo) -> None:
    pack = library.combine(*combo.codes)
    with pytest.raises(packs.PackError, match="missing_items"):
        pack.instructing_party_request(**{k: v for k, v in SLOTS.items() if k != "missing_items"})


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_quarantine_purge_days_come_from_the_jurisdiction_pack(library: packs.PackLibrary, combo: Combo) -> None:
    pack = library.combine(*combo.codes)
    assert pack.quarantine_purge_days == combo.raw("jurisdiction", "pack.yaml")["quarantine_purge_days"]


def test_launch_and_fixture_packs_differ(library: packs.PackLibrary) -> None:
    launch, fixture = library.combine(*LAUNCH.codes), library.combine(*FIXTURE.codes)
    assert launch.quarantine_purge_days != fixture.quarantine_purge_days
    assert launch.instructing_party_request(**SLOTS) != fixture.instructing_party_request(**SLOTS)
