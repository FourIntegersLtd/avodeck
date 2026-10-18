"""Architecture 10.11: production cells refuse unapproved packs.

Approval is `status: approved` with `approved_by` and `approved_on` in pack.yaml. A cell that sets
`PACKS_APPROVED_ONLY=1` loads only approved packs; other cells (local, test, staging) load drafts.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import copy_packs

APPROVAL = "status: approved\napproved_by: Reviewer One\napproved_on: 2026-10-01\n"


def fixture_packs(tmp_path: Path) -> list[Path]:
    _, fixtures = copy_packs(tmp_path)
    return sorted(fixtures.glob("*/*/pack.yaml"))


def approve(files: list[Path]) -> None:
    for path in files:
        path.write_text(path.read_text().rstrip("\n") + "\n" + APPROVAL)


def test_a_cell_that_allows_drafts_loads_unapproved_packs(tmp_path: Path) -> None:
    files = fixture_packs(tmp_path)
    packs.PackLibrary((files[0].parents[2],))


def test_an_approved_only_cell_refuses_a_pack_with_no_approval(tmp_path: Path) -> None:
    files = fixture_packs(tmp_path)
    approve(files[1:])
    with pytest.raises(packs.PackError, match="not approved"):
        packs.PackLibrary((files[0].parents[2],), approved_only=True)


def test_an_approved_only_cell_refuses_a_draft_even_with_a_reviewer(tmp_path: Path) -> None:
    files = fixture_packs(tmp_path)
    approve(files)
    files[0].write_text(files[0].read_text().replace("status: approved", "status: draft"))
    with pytest.raises(packs.PackError, match="not approved"):
        packs.PackLibrary((files[0].parents[2],), approved_only=True)


def test_an_approved_only_cell_loads_packs_with_reviewer_and_date(tmp_path: Path) -> None:
    files = fixture_packs(tmp_path)
    approve(files)
    packs.PackLibrary((files[0].parents[2],), approved_only=True)


def test_the_cell_setting_turns_the_check_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = fixture_packs(tmp_path)
    monkeypatch.setenv("PACKS_DIR", str(files[0].parents[2]))
    monkeypatch.setenv("PACKS_APPROVED_ONLY", "1")
    with pytest.raises(packs.PackError, match="not approved"):
        packs.default_library()
