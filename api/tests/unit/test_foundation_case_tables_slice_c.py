"""Slice C's case tables are registered for case deletion (docs/slices/0-foundation.md, changes for slice C)."""

from __future__ import annotations

from app.core.contracts.case_tables import CASE_TABLES

SLICE_C_CASE_TABLES = {
    "case_parties",
    "case_instructing_parties",
    "case_links",
    "case_instructions",
    "case_subject_keys",
}


def test_slice_c_case_tables_are_registered() -> None:
    assert SLICE_C_CASE_TABLES <= CASE_TABLES
