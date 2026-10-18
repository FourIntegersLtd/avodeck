-- 0206 reader (was 0395, which sat in slice C's range): slice B's case tables (page_reads, fact_verifications, fact_flags, 0202-0204) join the
-- purge order (app.case_purge_order(), migration 0017), with every table before them, in the same order as
-- `PURGE_ORDER` in api/app/core/contracts/case_tables.py: each child table before the table it references.
-- 0380 (slice C) replaces this list again; 0499 and 0599 restore every table, so the final order is complete.

set role app_migrator;

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'instruction_extractions', 'prefill_runs', 'prefill_suggestions', 'assumptions', 'case_instructions', 'jobs', 'fact_flags', 'fact_verifications', 'page_reads', 'facts', 'ocr_words', 'page_analysis', 'page_embeddings', 'page_quarantines', 'page_replacements', 'missing_items', 'record_gaps', 'document_parts', 'document_scans', 'exhibits', 'uploads', 'pages', 'questions', 'instruction_versions', 'documents', 'instructing_party_requests', 'case_processing', 'report_versions', 'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_parties', 'case_instructing_parties', 'case_links', 'case_subject_keys', 'case_withdrawals', 'conflict_answer_history', 'conflict_answers', 'conflict_declarations', 'case_members', 'legal_holds', 'case_keys', 'cases']
$$;

reset role;
