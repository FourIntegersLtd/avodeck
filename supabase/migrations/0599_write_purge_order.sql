-- 0599 write: slice E's case tables join the purge order (app.case_purge_order(), migration 0017; with
-- slices A and C, 0112 and 0380, since this is the last definition by number), in the same order as
-- `PURGE_ORDER` in api/app/core/contracts/case_tables.py: lint issues and opinion versions before
-- opinions, and every slice E table before the jobs, facts, documents and questions it references.

set role app_migrator;

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'instruction_extractions', 'prefill_runs', 'prefill_suggestions', 'assumptions', 'case_instructions', 'lint_issues', 'opinion_versions', 'opinions', 'points', 'range_notes', 'witness_claims', 'jobs', 'facts', 'ocr_words', 'page_analysis', 'page_embeddings', 'page_quarantines', 'page_replacements', 'missing_items', 'record_gaps', 'document_parts', 'document_scans', 'exhibits', 'uploads', 'pages', 'questions', 'instruction_versions', 'documents', 'instructing_party_requests', 'case_processing', 'report_versions', 'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_parties', 'case_instructing_parties', 'case_links', 'case_subject_keys', 'case_withdrawals', 'conflict_answer_history', 'conflict_answers', 'conflict_declarations', 'case_members', 'legal_holds', 'case_keys', 'cases']
$$;

reset role;
