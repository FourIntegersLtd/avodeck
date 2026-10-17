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
        "legal_holds",
        "notifications",
        "pages",
        "questions",
        "report_versions",
        "transcripts",
        "uploads",
    }
)

# Kept after the case is deleted: the append-only audit trail (it holds ids and short codes only, and
# its hash chain covers every column), the certificate of deletion and the deletion ledger (F29).
KEPT_AFTER_CASE_DELETION: frozenset[str] = frozenset({"audit_log", "deletion_certificates", "deletion_ledger"})

# Registered here before the slice named creates the table in its migration range. Remove the entry
# in the same change that creates the table.
AWAITING_SLICE_MIGRATION: dict[str, str] = {}

# The order `app.purge_case()` removes a case's rows in: each child table before the tables it
# references, `cases` (left as a tombstone) last. The database keeps the same list in
# `app.case_purge_order()`; a slice adding a case table adds it to both.
PURGE_ORDER: tuple[str, ...] = (
    "job_steps",
    "jobs",
    "facts",
    "uploads",
    "pages",
    "documents",
    "report_versions",
    "questions",
    "transcripts",
    "ai_calls",
    "notifications",
    "idempotency_keys",
    "case_members",
    "legal_holds",
    "case_keys",
    "cases",
)
