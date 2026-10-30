"""A declaration whose wording is not yet checked by a lawyer (`verify_before_launch: true`) never reaches production.

`PACKS_APPROVED_ONLY=1` refuses it (`PackNotApproved`); other cells log a warning naming the key.
A checked declaration (`verify_before_launch: false`) must say who checked it and when.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from app.core import packs
from tests.support.packs import copy_packs

APPROVAL = "status: approved\napproved_by: Reviewer One\napproved_on: 2026-10-01\n"
SECRET_WORDING = "I affirm this test report is mine."


def prepared(tmp_path: Path, front: str) -> tuple[Path, Path]:
    """Fixture packs, all approved, with the jurisdiction declaration's front matter replaced."""
    _, fixtures = copy_packs(tmp_path)
    for pack_yaml in fixtures.glob("*/*/pack.yaml"):
        pack_yaml.write_text(pack_yaml.read_text().rstrip("\n") + "\n" + APPROVAL)
    declaration = fixtures / "jurisdiction" / "zz-test" / "declarations.md"
    declaration.write_text(f"---\nkey: affirmation\nrule_ref: \"Rule Z.9\"\nversion: 0.0.1\n{front}---\n{SECRET_WORDING}\n")
    return fixtures, declaration


def test_approved_only_refuses_an_unverified_declaration(tmp_path: Path) -> None:
    fixtures, _ = prepared(tmp_path, "verify_before_launch: true\n")
    with pytest.raises(packs.PackNotApproved, match="affirmation"):
        packs.PackLibrary((fixtures,), approved_only=True)


def test_pack_not_approved_is_a_pack_error(tmp_path: Path) -> None:
    assert issubclass(packs.PackNotApproved, packs.PackError)


def test_approved_only_loads_a_verified_declaration(tmp_path: Path) -> None:
    fixtures, _ = prepared(tmp_path, "verify_before_launch: false\nverified_by: Barrister, Example Chambers\nverified_on: 2026-10-20\n")
    packs.PackLibrary((fixtures,), approved_only=True)


def test_approved_only_loads_a_declaration_that_has_no_flag(tmp_path: Path) -> None:
    fixtures, _ = prepared(tmp_path, "")
    packs.PackLibrary((fixtures,), approved_only=True)


def test_other_cells_load_an_unverified_declaration_and_warn_with_the_key_only(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    fixtures, _ = prepared(tmp_path, "verify_before_launch: true\n")
    with caplog.at_level(logging.WARNING):
        packs.PackLibrary((fixtures,))
    assert "affirmation" in caplog.text
    assert SECRET_WORDING not in caplog.text


def test_the_schema_rejects_false_without_verified_on(tmp_path: Path) -> None:
    fixtures, _ = prepared(tmp_path, "verify_before_launch: false\nverified_by: Barrister, Example Chambers\n")
    with pytest.raises(packs.PackError, match="verified_on"):
        packs.PackLibrary((fixtures,))


def test_the_schema_rejects_false_without_verified_by(tmp_path: Path) -> None:
    fixtures, _ = prepared(tmp_path, "verify_before_launch: false\nverified_on: 2026-10-20\n")
    with pytest.raises(packs.PackError, match="verified_by"):
        packs.PackLibrary((fixtures,))


def test_the_schema_rejects_a_verified_on_that_is_not_a_date(tmp_path: Path) -> None:
    fixtures, _ = prepared(tmp_path, "verify_before_launch: false\nverified_by: Barrister\nverified_on: \"last week\"\n")
    with pytest.raises(packs.PackError):
        packs.PackLibrary((fixtures,))
