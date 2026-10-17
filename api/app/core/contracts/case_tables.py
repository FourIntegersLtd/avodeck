"""Every table holding a case's data (slice H deletes a case through this registry).

`tests/repository/test_foundation_contract_tables.py` fails if a table with a `case_id` column is
missing here, so each slice adds its case tables when it adds them.
"""

from __future__ import annotations

CASE_TABLES: frozenset[str] = frozenset(
    {
        "case_processing",
        "page_quarantines",
        "cases",
        "ai_calls",
        "assumptions",
        "case_instructing_parties",
        "case_instructions",
        "case_keys",
        "case_links",
        "case_members",
        "case_parties",
        "case_subject_keys",
        "case_withdrawals",
        "conflict_answer_history",
        "conflict_answers",
        "conflict_declarations",
        "document_scans",
        "documents",
        "facts",
        "idempotency_keys",
        "instruction_extractions",
        "instruction_versions",
        "instructing_party_requests",
        "job_steps",
        "jobs",
        "legal_holds",
        "missing_items",
        "notifications",
        "ocr_words",
        "page_analysis",
        "pages",
        "prefill_runs",
        "prefill_suggestions",
        "questions",
        "record_gaps",
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
    "instruction_extractions",
    "prefill_runs",
    "prefill_suggestions",
    "assumptions",
    "case_instructions",
    "jobs",
    "facts",
    "uploads",
    "pages",
    "questions",
    "instruction_versions",
    "documents",
    "report_versions",
    "transcripts",
    "ai_calls",
    "notifications",
    "idempotency_keys",
    "case_parties",
    "case_instructing_parties",
    "case_links",
    "case_subject_keys",
    "case_withdrawals",
    "conflict_answer_history",
    "conflict_answers",
    "conflict_declarations",
    "case_members",
    "legal_holds",
    "case_keys",
    "cases",
)
