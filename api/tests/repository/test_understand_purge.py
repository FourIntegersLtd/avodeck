"""Slice D's case tables are deleted with the case (slice D brief, "Purge"; architecture 4.5).

Each D table is in the case-table registry and in the purge order before the facts, questions and
jobs it references; the database purges in that same order.
"""

from __future__ import annotations

import psycopg

from app.core.contracts.case_tables import CASE_TABLES, PURGE_ORDER

UNDERSTAND_TABLES = (
    "fact_question_links",
    "availability_overrides",
    "findings",
    "check_runs",
    "consultation_notes",
    "comments",
    "case_guideline_versions",
)


def test_understand_tables_are_case_tables_purged_before_what_they_reference() -> None:
    assert set(UNDERSTAND_TABLES) <= CASE_TABLES
    for table in UNDERSTAND_TABLES:
        for parent in ("facts", "questions", "jobs"):
            assert PURGE_ORDER.index(table) < PURGE_ORDER.index(parent), (table, parent)
    assert PURGE_ORDER.index("findings") < PURGE_ORDER.index("check_runs")


def test_the_database_purges_understand_tables_in_the_same_order(admin: psycopg.Connection) -> None:
    row = admin.execute("select app.case_purge_order()").fetchone()
    assert row is not None
    assert [t for t in row[0] if t in UNDERSTAND_TABLES] == [t for t in PURGE_ORDER if t in UNDERSTAND_TABLES]
    assert set(UNDERSTAND_TABLES) <= set(row[0])
