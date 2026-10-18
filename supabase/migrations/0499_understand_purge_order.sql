-- 0499 understand: slice D's case tables join the purge order (app.case_purge_order(), migration 0017),
-- in the same order as `PURGE_ORDER` in api/app/core/contracts/case_tables.py: each child table before
-- the table it references (links, overrides, findings, notes before facts and questions; findings before
-- check_runs, which comes before jobs). Keep this the highest-numbered definer; a later slice redefines it with its own tables.

set role app_migrator;

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'instruction_extractions', 'prefill_runs', 'prefill_suggestions', 'assumptions', 'case_instructions', 'fact_question_links', 'availability_overrides', 'findings', 'check_runs', 'consultation_notes', 'comments', 'case_guideline_versions', 'jobs', 'facts', 'ocr_words', 'page_analysis', 'page_embeddings', 'page_quarantines', 'page_replacements', 'missing_items', 'record_gaps', 'document_parts', 'document_scans', 'exhibits', 'uploads', 'pages', 'questions', 'instruction_versions', 'documents', 'instructing_party_requests', 'case_processing', 'report_versions', 'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_parties', 'case_instructing_parties', 'case_links', 'case_subject_keys', 'case_withdrawals', 'conflict_answer_history', 'conflict_answers', 'conflict_declarations', 'case_members', 'legal_holds', 'case_keys', 'cases']
$$;

reset role;
