"""Invoice wording and the estimate threshold come from the jurisdiction pack (`invoice.yaml`), under both
pack combinations: `invoice_statements()` (lines every invoice prints, such as fee independence) and
`estimate_overrun_percent()` (how far over the time estimate before the time screen suggests telling the
instructing party). A pack with no `invoice.yaml` prints nothing and shows no notice. Expected values are read
from the pack files, so nothing here names a country.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import BOTH, FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, Combo, copy_packs


@pytest.fixture(scope="module")
def library() -> packs.PackLibrary:
    return packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT)


@pytest.mark.parametrize("combo", BOTH, ids=[c.jurisdiction for c in BOTH])
def test_invoice_statements_and_threshold_come_from_the_jurisdiction(library: packs.PackLibrary, combo: Combo) -> None:
    raw = combo.raw("jurisdiction", "invoice.yaml") or {}
    pack = library.combine(*combo.codes)
    assert pack.invoice_statements() == list(raw.get("statements", []))
    assert pack.estimate_overrun_percent() == raw.get("estimate_overrun_percent")


def test_the_launch_and_fixture_packs_differ(library: packs.PackLibrary) -> None:
    launch, fixture = library.combine(*LAUNCH.codes), library.combine(*FIXTURE.codes)
    assert launch.invoice_statements(), "the launch jurisdiction states that fees do not depend on the outcome"
    assert fixture.invoice_statements() == []
    assert launch.estimate_overrun_percent() != fixture.estimate_overrun_percent()


def test_a_pack_without_an_invoice_file_prints_nothing(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    (fixture_root / "jurisdiction" / FIXTURE.jurisdiction / "invoice.yaml").unlink()
    pack = packs.load_library(launch_root, fixture_root).combine(*FIXTURE.codes)
    assert pack.invoice_statements() == []
    assert pack.estimate_overrun_percent() is None


def test_an_invoice_file_that_breaks_its_schema_is_refused(tmp_path: Path) -> None:
    launch_root, fixture_root = copy_packs(tmp_path)
    (fixture_root / "jurisdiction" / FIXTURE.jurisdiction / "invoice.yaml").write_text("statements: [1]\n")
    with pytest.raises(packs.PackError, match="invoice.yaml"):
        packs.load_library(launch_root, fixture_root)


def test_the_launch_invoice_states_its_payment_terms_as_well(library: packs.PackLibrary) -> None:
    """Screen 18b shows the payment terms beside the outcome statement: both are pack lines frozen on the invoice."""
    assert len(library.combine(*LAUNCH.codes).invoice_statements()) >= 2
