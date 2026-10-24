"""The support console's court-wording fingerprint is slice F's (support plan phase 2; F34).

Slices may not import each other, so organisations keeps its own copy of the sum the sign step compares.
This holds the two equal under the launch packs and the fixture packs, so a change in one fails here.
"""

from __future__ import annotations

import pytest

from app.core import packs
from app.features.organisations.support import declaration_version as support_version
from app.features.report.checks import declaration_version as report_version
from tests.support.packs import FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo


@pytest.mark.parametrize("combo", [LAUNCH, FIXTURE], ids=["launch", "fixture"])
def test_the_support_fingerprint_matches_the_sign_step(combo: Combo) -> None:
    pack = packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*combo.codes)
    assert pack.declarations()
    assert support_version(pack) == report_version(pack)
