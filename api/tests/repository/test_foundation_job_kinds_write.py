"""Migration 0023: the opinion jobs say which case action queues them (rule 9)."""

from __future__ import annotations

import psycopg


def test_suggest_points_and_witness_comparison_need_the_opinion_action(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select kind, action from app.job_kind_actions"
        " where kind in ('suggest_points', 'compare_witness_statement') order by kind"
    ).fetchall()
    assert rows == [("compare_witness_statement", "opinion.write"), ("suggest_points", "opinion.write")]
