"""Reference tables a check names (`table` in checks.yaml), read through `MergedPack.reference_table(name)`.

A slice never reaches into the pack's files: it asks for a table by the name a check gives, and gets the
table's rows after the layer merge (copies, so a caller cannot change the pack). Launch values are read from
the pack files; the fixture table is written here (rule 11).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.core import packs
from tests.support.packs import FIXTURE, FIXTURE_ROOT, LAUNCH, LAUNCH_ROOT, copy_packs, write

FIXTURE_OVERLAY = "overlay/zz-test/zz-specialty"


def rows_in(raw: dict[str, Any]) -> list[Any]:
    return next(v for v in raw.values() if isinstance(v, list))


def fixture_with_table(fixtures: Path, rows: str | None = "  - {code: zz1, every_days: 30}\n") -> packs.MergedPack:
    """A fixture overlay whose check names `drug_monitoring`; `rows=None` leaves the table file out."""
    write(fixtures / FIXTURE_OVERLAY / "pack.yaml", "jurisdiction: zz-test\nspecialty: zz-specialty\nversion: 0.0.1\n")
    write(
        fixtures / FIXTURE_OVERLAY / "checks.yaml",
        "checks:\n  - {key: zz_overdue, rule: monitoring_overdue, applies_to: measurement, table: drug_monitoring}\n",
    )
    if rows is not None:
        write(fixtures / FIXTURE_OVERLAY / "drug_monitoring.yaml", "version: 0.0.1\ndrugs:\n" + rows)
    return packs.load_library(fixtures).combine(*FIXTURE.codes)


def test_launch_tables_named_by_checks_come_from_the_pack_file() -> None:
    pack = packs.load_library(LAUNCH_ROOT, FIXTURE_ROOT).combine(*LAUNCH.codes)
    named = {str(c["table"]) for c in pack.checks() if c.get("table")}
    assert named, "the launch packs name at least one reference table"
    for name in named:
        assert pack.reference_table(name) == rows_in(LAUNCH.raw("overlay", f"{name}.yaml"))


def test_fixture_table_rows_are_returned_as_copies(tmp_path: Path) -> None:
    pack = fixture_with_table(copy_packs(tmp_path)[1])
    rows = pack.reference_table("drug_monitoring")
    assert rows == [{"code": "zz1", "every_days": 30}]
    rows[0]["every_days"] = 1
    rows.append({"code": "zz2"})
    assert pack.reference_table("drug_monitoring") == [{"code": "zz1", "every_days": 30}]


def test_a_named_table_with_no_file_has_no_rows(tmp_path: Path) -> None:
    pack = fixture_with_table(copy_packs(tmp_path)[1], rows=None)
    assert pack.reference_table("drug_monitoring") == []


def test_only_tables_a_check_names_can_be_read() -> None:
    pack = packs.load_library(FIXTURE_ROOT).combine(*FIXTURE.codes)
    for name in ("facts", "vocabulary", "zz_nothing"):
        with pytest.raises(KeyError):
            pack.reference_table(name)
