"""One or more rows in every case table, for the deletion guard (tests/guards/test_foundation_case_purge.py).

A slice that adds a case table adds a seeder here (as the local admin role), so the guard proves
`app.purge_case()` removes it. Values are made up and short; nothing here is a real record.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from tests.support.contracts import add_fact, add_question, add_report_version
from tests.support.seed import Seed


@dataclass(frozen=True)
class CaseWorld:
    case: uuid.UUID
    expert: uuid.UUID
    org: uuid.UUID


Seeder = Callable[[psycopg.Connection, Seed, CaseWorld], int]


def _documents_pages_facts(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    doc = seed.document(w.case)
    first = seed.page(w.case, doc, 1)
    second = seed.page(w.case, doc, 2)
    admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (first, second))
    add_fact(admin, w.case, first, status="verified")
    add_fact(admin, w.case, second)
    admin.execute(
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " document_id) values (%s, %s, 'records', 'f.pdf', 10, %s, 8, 2, %s)",
        (w.case, w.expert, "0" * 64, doc),
    )
    return {"documents": 1, "pages": 2, "facts": 2, "uploads": 1}


def _jobs(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    job = seed.job(w.case)
    admin.execute("insert into app.job_steps (job_id, case_id, step) values (%s, %s, 'zz_step')", (job, w.case))
    return {"jobs": 1, "job_steps": 1}


def _reports(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    served = add_report_version(admin, w.case, status="served")
    draft = add_report_version(admin, w.case, version_no=2)
    admin.execute("update app.report_versions set prev_version_id = %s where id = %s", (served, draft))
    return {"report_versions": 2}


def _questions(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    add_question(admin, w.case)
    return {"questions": 1}


def _people_rows(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    helper = seed.person()
    seed.membership(w.org, helper, "assistant")
    seed.case_member(w.case, helper, "assistant", added_by=w.expert)
    seed.notification(w.expert, w.org, w.case)
    admin.execute(
        "insert into app.transcripts (case_id, person_id, context, text, duration_seconds)"
        " values (%s, %s, 'zz_note', %s, 3)",
        (w.case, w.expert, b"sealed"),
    )
    admin.execute(
        "insert into app.ai_calls (case_id, task_id, model_id, region, prompt_version, input_tokens, output_tokens,"
        " outcome) values (%s, 'zz_task', 'zz-model', 'zz', 'v1', 1, 1, 'ok')",
        (w.case,),
    )
    admin.execute(
        "insert into app.idempotency_keys (person_id, key, fingerprint, status_code, response_body, case_id)"
        " values (%s, %s, 'f', 200, %s, %s)",
        (w.expert, f"k-{uuid.uuid4().hex}", b"{}", w.case),
    )
    return {"case_members": 1, "notifications": 1, "transcripts": 1, "ai_calls": 1, "idempotency_keys": 1}


def _legal_holds(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """A hold that has ended (an active one would stop the purge)."""
    admin.execute(
        "insert into app.legal_holds (case_id, reason, set_by, until, released_at, released_by)"
        " values (%s, 'zz_appeal', %s, current_date - 1, now(), %s)",
        (w.case, w.expert, w.expert),
    )
    return {"legal_holds": 1}


def _deletion_certificates(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """Kept after the purge (KEPT_AFTER_CASE_DELETION): counts only, no patient text."""
    admin.execute("insert into app.deletion_certificates (case_id, counts) values (%s, '{\"pages\": 2}')", (w.case,))
    return {"deletion_certificates": 1}


def _setup_rows(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """Slice C's case tables (migrations 0019, 0310-0370)."""
    version = admin.execute(
        "insert into app.instruction_versions (case_id, version_no, kind) values (%s, 1, 'letter') returning id",
        (w.case,),
    ).fetchone()
    assert version is not None
    v = version[0]
    admin.execute(
        "insert into app.assumptions (case_id, instruction_version_id, number, text_verbatim)"
        " values (%s, %s, 1, 'zz assumption')",
        (w.case, v),
    )
    admin.execute(
        "insert into app.case_instructions (case_id, instruction_version_id, text, received_on)"
        " values (%s, %s, %s, current_date)",
        (w.case, v, b"sealed"),
    )
    admin.execute(
        "insert into app.instruction_extractions (instruction_version_id, case_id, questions, assumptions, rejected,"
        " model_task, prompt_version) values (%s, %s, 1, 1, 0, 'T-06', 'v1')",
        (v, w.case),
    )
    admin.execute(
        "insert into app.prefill_suggestions (case_id, instruction_version_id, field, value, word_ids, model_task,"
        " prompt_version) values (%s, %s, 'court_track', %s, '{w1}', 'T-04', 'v1')",
        (w.case, v, b"sealed"),
    )
    admin.execute("insert into app.prefill_runs (case_id, kept, rejected) values (%s, 1, 0)", (w.case,))
    admin.execute(
        "insert into app.case_parties (case_id, role_key, name) values (%s, 'zz_party', %s)", (w.case, b"sealed")
    )
    admin.execute(
        "insert into app.case_instructing_parties (case_id, type_key, name, firm, email)"
        " values (%s, 'zz_firm', 'Zed', 'Zed Firm', 'zed@example.test')",
        (w.case,),
    )
    other = seed.case(w.org, w.expert)
    admin.execute(
        "insert into app.case_links (case_id, linked_case_id, kind) values (%s, %s, 'related')", (w.case, other)
    )
    admin.execute("insert into app.case_subject_keys (case_id, key_hash) values (%s, %s)", (w.case, "a" * 64))
    admin.execute("insert into app.case_withdrawals (case_id, reason) values (%s, %s)", (w.case, b"sealed"))
    admin.execute(
        "insert into app.conflict_answers (case_id, question_key, answer) values (%s, 'zz_known', 'no')", (w.case,)
    )
    admin.execute("insert into app.conflict_declarations (case_id, outcome) values (%s, 'none')", (w.case,))
    return {
        "instruction_versions": 1,
        "assumptions": 1,
        "case_instructions": 1,
        "instruction_extractions": 1,
        "prefill_suggestions": 1,
        "prefill_runs": 1,
        "case_parties": 1,
        "case_instructing_parties": 1,
        "case_links": 1,
        "case_subject_keys": 1,
        "case_withdrawals": 1,
        "conflict_answers": 1,
        "conflict_answer_history": 1,
        "conflict_declarations": 1,
    }


def _pipeline_tables(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """Slice A's case tables (migrations 0100-0199): one made-up row each."""
    doc = seed.document(w.case)
    part = seed.document(w.case)
    page = seed.page(w.case, doc, 101)
    new_page = seed.page(w.case, part, 102)
    admin.execute("insert into app.case_processing (case_id) values (%s)", (w.case,))
    admin.execute(
        "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
        " values (%s, %s, 1, 'w1', %s, array[0,0,1,0,1,1,0,1]::real[], 0.9, 'ocr')",
        (page, w.case, b"sealed"),
    )
    admin.execute("insert into app.page_analysis (page_id, case_id) values (%s, %s)", (page, w.case))
    admin.execute(
        "insert into app.page_embeddings (page_id, case_id, model, embedding)"
        " values (%s, %s, 'zz-model', array_fill(0.0::real, array[384])::extensions.vector)",
        (page, w.case),
    )
    admin.execute(
        "insert into app.page_quarantines (case_id, page_id, reason) values (%s, %s, 'zz_reason')", (w.case, page)
    )
    admin.execute(
        "insert into app.page_replacements (case_id, page_id, document_id, requested_by, new_page_id)"
        " values (%s, %s, %s, %s, %s)",
        (w.case, page, part, w.expert, new_page),
    )
    admin.execute(
        "insert into app.missing_items (case_id, citing_page_id, reference_key, word_ids, description)"
        " values (%s, %s, 'r1', array['w1'], %s)",
        (w.case, page, b"sealed"),
    )
    admin.execute(
        "insert into app.record_gaps (case_id, source_type, fact_kind, gap_start, gap_end)"
        " values (%s, 'zz_source', 'zz_kind', current_date - 30, current_date)",
        (w.case,),
    )
    admin.execute(
        "insert into app.document_parts (case_id, parent_document_id, document_id, part_no, media_type)"
        " values (%s, %s, %s, 1, 'application/pdf')",
        (w.case, doc, part),
    )
    admin.execute("insert into app.document_scans (document_id, case_id, clean) values (%s, %s, true)", (doc, w.case))
    admin.execute(
        "insert into app.exhibits (case_id, document_id, media_type, size, sha256, storage_path)"
        " values (%s, %s, 'image/heic', 1, %s, 'zz/path')",
        (w.case, part, "0" * 64),
    )
    admin.execute(
        "insert into app.instructing_party_requests (case_id, subject, body_text, sent_via, created_by)"
        " values (%s, %s, %s, 'mailto', %s)",
        (w.case, b"sealed", b"sealed", w.expert),
    )
    return {
        "documents": 2,
        "pages": 2,
        "case_processing": 1,
        "ocr_words": 1,
        "page_analysis": 1,
        "page_embeddings": 1,
        "page_quarantines": 1,
        "page_replacements": 1,
        "missing_items": 1,
        "record_gaps": 1,
        "document_parts": 1,
        "document_scans": 1,
        "exhibits": 1,
        "instructing_party_requests": 1,
    }


def _reader_logs(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    page = seed.page(w.case, seed.document(w.case), 900)
    fact = add_fact(admin, w.case, page, status="verified")
    admin.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 20)",
        (w.case, page, w.expert),
    )
    admin.execute(
        "insert into app.fact_verifications (case_id, fact_id, person_id, action) values (%s, %s, %s, 'correct')",
        (w.case, fact, w.expert),
    )
    admin.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, 'points', %s)",
        (w.case, fact, w.expert),
    )
    return {"documents": 1, "pages": 1, "facts": 1, "page_reads": 1, "fact_verifications": 1, "fact_flags": 1}


def _understand_rows(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """Slice D's case tables (migration 0400)."""
    page = seed.page(w.case, seed.document(w.case), 9)
    fact = add_fact(admin, w.case, page, status="verified")
    question = add_question(admin, w.case)
    admin.execute(
        "insert into app.fact_question_links (case_id, fact_id, question_id, source) values (%s, %s, %s, 'ai')",
        (w.case, fact, question),
    )
    admin.execute(
        "insert into app.availability_overrides (case_id, fact_id, unknown, reason, person_id)"
        " values (%s, %s, true, 'zz', %s)",
        (w.case, fact, w.expert),
    )
    run = admin.execute(
        "insert into app.check_runs (case_id, pack_version) values (%s, 'zz@1') returning id", (w.case,)
    ).fetchone()
    assert run is not None
    admin.execute(
        "insert into app.findings (case_id, check_key, rule, evidence_fact_ids, evidence_page_ids, pack_version,"
        " run_id) values (%s, 'zz_check', 'zz_rule', %s, %s, 'zz@1', %s)",
        (w.case, [fact], [page], run[0]),
    )
    admin.execute(
        "insert into app.case_guideline_versions (case_id, code, version_label) values (%s, 'zz_g1', 'v1')",
        (w.case,),
    )
    admin.execute(
        "insert into app.consultation_notes (case_id, fact_id, body, person_id) values (%s, %s, %s, %s)",
        (w.case, fact, "zz note", w.expert),
    )
    admin.execute(
        "insert into app.comments (case_id, target_kind, target_id, body, author_id) values (%s, 'page', %s, 'zz', %s)",
        (w.case, str(page), w.expert),
    )
    return {
        "documents": 1,
        "pages": 1,
        "facts": 1,
        "questions": 1,
        "fact_question_links": 1,
        "availability_overrides": 1,
        "check_runs": 1,
        "findings": 1,
        "case_guideline_versions": 1,
        "consultation_notes": 1,
        "comments": 1,
    }


def _write_tables(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """Slice E's case tables (migrations 0500-0599): one made-up row each; the opinion's version comes
    from its trigger."""
    doc = seed.document(w.case)
    page = seed.page(w.case, doc, 501)
    fact = add_fact(admin, w.case, page, status="verified")
    question = add_question(admin, w.case, number=50, status="confirmed")
    admin.execute(
        "insert into app.points (case_id, question_id, fact_id, side, status, origin, fact_version)"
        " values (%s, %s, %s, 'neutral', 'accepted', 'expert', 2)",
        (w.case, question, fact),
    )
    admin.execute(
        "insert into app.range_notes (case_id, question_id, text) values (%s, %s, %s)", (w.case, question, b"sealed")
    )
    admin.execute(
        "insert into app.witness_claims (case_id, document_id, claim_index, text_verbatim, word_ids, verdict,"
        " fact_ids, fact_versions) values (%s, %s, 0, %s, '{w1}', 'contradicts', array[%s]::uuid[], '{2}')",
        (w.case, doc, b"sealed", fact),
    )
    row = admin.execute(
        "insert into app.opinions (case_id, question_id, body) values (%s, %s, %s) returning id",
        (w.case, question, b"sealed"),
    ).fetchone()
    assert row is not None
    admin.execute(
        "insert into app.lint_issues (case_id, opinion_id, opinion_version, sentence_index, type, words, source)"
        " values (%s, %s, 1, 0, 'uncited_fact', %s, 'regex')",
        (w.case, row[0], b"sealed"),
    )
    admin.execute(
        "insert into app.relevant_facts (case_id, question_id, fact_id, why_relevant, fact_version)"
        " values (%s, %s, %s, 'advice to return', 2)",
        (w.case, question, fact),
    )
    admin.execute("insert into app.witness_comparisons (case_id, document_id) values (%s, %s)", (w.case, doc))
    return {
        "documents": 1,
        "pages": 1,
        "facts": 1,
        "questions": 1,
        "points": 1,
        "range_notes": 1,
        "witness_claims": 1,
        "opinions": 1,
        "opinion_versions": 1,
        "lint_issues": 1,
        "relevant_facts": 1,
        "witness_comparisons": 1,
    }


def _report_tables(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """Slice F's case tables (migrations 0600-0699): one made-up row each, on a draft and a served version."""
    doc = seed.document(w.case)
    page = seed.page(w.case, doc, 601)
    fact = add_fact(admin, w.case, page, status="verified")
    question = add_question(admin, w.case, number=60, status="confirmed")
    draft = add_report_version(admin, w.case, version_no=60)
    served = add_report_version(admin, w.case, version_no=61, status="served")
    row = admin.execute(
        "insert into app.report_paragraphs (case_id, report_version_id, para_no, section, content_control_id, text,"
        " question_id) values (%s, %s, 1, 'opinion', 'cc1', %s, %s) returning id",
        (w.case, draft, b"sealed", question),
    ).fetchone()
    assert row is not None
    admin.execute(
        "insert into app.citations (case_id, report_version_id, paragraph_id, cite_no, fact_id, fact_version,"
        " value_snapshot, quote_verbatim, page_id) values (%s, %s, %s, 1, %s, 2, %s, %s, %s)",
        (w.case, draft, row[0], fact, b"sealed", b"sealed", page),
    )
    admin.execute(
        "insert into app.final_check_runs (case_id, report_version_id, results, passed) values (%s, %s, '[]', false)",
        (w.case, draft),
    )
    admin.execute(
        "insert into app.report_snapshots (case_id, report_version_id, kind, sha256) values (%s, %s, 'zz_kind', %s)",
        (w.case, draft, "b" * 64),
    )
    admin.execute(
        "insert into app.report_servings (case_id, report_version_id, served_on, sent_via)"
        " values (%s, %s, current_date, 'email')",
        (w.case, served),
    )
    admin.execute(
        "insert into app.spot_checks (case_id, report_version_id, reviewer_email, fact_ids, token_hash, expires_at,"
        " created_by) values (%s, %s, 'z@example.test', array[%s]::uuid[], %s, now(), %s)",
        (w.case, draft, fact, uuid.uuid4().hex * 2, w.expert),
    )
    admin.execute(
        "insert into app.time_entries (case_id, person_id, stage, started_at, ended_at, seconds)"
        " values (%s, %s, 'reading', now(), now(), 0)",
        (w.case, w.expert),
    )
    admin.execute("insert into app.report_settings (case_id) values (%s)", (w.case,))
    profile = admin.execute(
        "insert into app.billing_profiles (owner_type, owner_id, legal_name, currency) values ('person', %s, 'Z', 'XTS')"
        " on conflict (owner_type, owner_id) do update set legal_name = excluded.legal_name returning id",
        (w.expert,),
    ).fetchone()
    assert profile is not None
    invoice = admin.execute(
        "insert into app.invoices (case_id, billing_profile_id, lines, total, currency) values (%s, %s, '[]', 10, 'XTS')"
        " returning id",
        (w.case, profile[0]),
    ).fetchone()
    assert invoice is not None
    admin.execute("update app.invoices set status = 'sent' where id = %s", (invoice[0],))
    admin.execute(
        "insert into app.credit_notes (case_id, invoice_id, amount, reason) values (%s, %s, 1, 'zz_reason')",
        (w.case, invoice[0]),
    )
    return {
        "documents": 1,
        "pages": 1,
        "facts": 1,
        "questions": 1,
        "report_versions": 2,
        "report_paragraphs": 1,
        "citations": 1,
        "final_check_runs": 1,
        "report_snapshots": 1,
        "report_servings": 1,
        "spot_checks": 1,
        "time_entries": 1,
        "report_settings": 1,
        "invoices": 1,
        "credit_notes": 1,
    }


SEEDERS = (
    _documents_pages_facts,
    _jobs,
    _reports,
    _questions,
    _people_rows,
    _legal_holds,
    _deletion_certificates,
    _setup_rows,
    _pipeline_tables,
    _reader_logs,
    _understand_rows,
    _write_tables,
    _report_tables,
)


def fill_every_case_table(admin: psycopg.Connection, seed: Seed, world: CaseWorld) -> dict[str, int]:
    """Rows in every case table except `cases` and `case_keys`; returns how many went in each."""
    counts: dict[str, int] = {}
    for seeder in SEEDERS:
        for table, n in seeder(admin, seed, world).items():
            counts[table] = counts.get(table, 0) + n
    return counts
