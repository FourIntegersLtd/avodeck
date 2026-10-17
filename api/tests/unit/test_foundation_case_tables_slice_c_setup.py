"""Slice C's later case tables (migrations 0320-0340) are registered for case deletion
(docs/slices/0-foundation.md, changes for slice C)."""

from __future__ import annotations

from app.core.contracts.case_tables import CASE_TABLES

SLICE_C_LATER_CASE_TABLES = {
    "conflict_answers",
    "conflict_declarations",
    "case_withdrawals",
    "assumptions",
    "instruction_extractions",
    "prefill_suggestions",
    "prefill_runs",
}


def test_slice_c_later_case_tables_are_registered() -> None:
    assert SLICE_C_LATER_CASE_TABLES <= CASE_TABLES
