"""Every table holding a case's data (slice H deletes a case through this registry).

`tests/repository/test_foundation_contract_tables.py` fails if a table with a `case_id` column is
missing here, so each slice adds its case tables when it adds them.
"""

from __future__ import annotations

CASE_TABLES: frozenset[str] = frozenset(
    {
        "cases",
        "ai_calls",
        "case_keys",
        "case_members",
        "documents",
        "facts",
        "idempotency_keys",
        "job_steps",
        "jobs",
        "notifications",
        "pages",
        "questions",
        "report_versions",
        "transcripts",
        "uploads",
    }
)

# Kept after the case is deleted: the append-only audit trail (it holds ids and short codes only).
KEPT_AFTER_CASE_DELETION: frozenset[str] = frozenset({"audit_log"})
