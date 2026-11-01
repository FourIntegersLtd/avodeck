"""ADR 0034 (Decision B): which case roles carry blind read comes from the case-type pack.

The gate's HTTP behaviour is in test_foundation_blind_read.py; here the pack side:
- both packs keep named_expert in blind_read_duties and verification_duties;
- a pack may add a role, and the added role is then listed;
- a pack that drops named_expert is refused when loaded.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from app.core import packs
from tests.support.packs import BOTH, FIXTURE, LAUNCH, Combo, copy_packs


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
def test_named_expert_always_carries_both_duties(combo: Combo) -> None:
    library = packs.load_library(LAUNCH.root, FIXTURE.root)
    pack = library.combine(*combo.codes)
    assert "named_expert" in pack.blind_read_duties()
    assert "named_expert" in pack.verification_duties()


def test_the_launch_pack_gives_both_duties_to_the_named_expert_only() -> None:
    pack = packs.load_library(LAUNCH.root, FIXTURE.root).combine(*LAUNCH.codes)
    assert pack.blind_read_duties() == ["named_expert"]
    assert pack.verification_duties() == ["named_expert"]


def _edit_fixture_pack(tmp_path: Path, **changes: list[str]) -> tuple[Path, Path]:
    launch, fixture = copy_packs(tmp_path)
    path = fixture / "case-type" / FIXTURE.case_type / "pack.yaml"
    meta = yaml.safe_load(path.read_text())
    meta.update(changes)
    path.write_text(yaml.safe_dump(meta))
    return launch, fixture


def test_a_pack_may_add_a_role_to_blind_read_duties(tmp_path: Path) -> None:
    launch, fixture = _edit_fixture_pack(tmp_path, blind_read_duties=["named_expert", "co_expert"])
    pack = packs.load_library(launch, fixture).combine(*FIXTURE.codes)
    assert pack.blind_read_duties() == ["named_expert", "co_expert"]


@pytest.mark.parametrize("duty", ["blind_read_duties", "verification_duties"])
def test_a_pack_without_the_named_expert_is_refused(tmp_path: Path, duty: str) -> None:
    launch, fixture = _edit_fixture_pack(tmp_path, **{duty: ["co_expert"]})
    with pytest.raises(packs.PackError, match="named_expert"):
        packs.load_library(launch, fixture).combine(*FIXTURE.codes)
