"""Slice C's conflict answer history (migration 0370) is registered for case deletion
(docs/slices/0-foundation.md, changes for slice C; review finding 7)."""

from __future__ import annotations

from app.core.contracts.case_tables import CASE_TABLES


def test_conflict_answer_history_is_registered() -> None:
    assert "conflict_answer_history" in CASE_TABLES
