-- 0699 report: slice F's case tables join the purge order (app.case_purge_order(), migration 0017; last
-- defined in 0599), in the same order as `PURGE_ORDER` in api/app/core/contracts/case_tables.py: citations
-- before paragraphs, every slice F table before the jobs, facts, pages, questions, report versions and
-- parties it references. billing_profiles is not a case table (it outlives the case).

set role app_migrator;

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'instruction_extractions', 'prefill_runs', 'prefill_suggestions', 'assumptions', 'case_instructions', 'fact_question_links', 'availability_overrides', 'findings', 'check_runs', 'consultation_notes', 'comments', 'case_guideline_versions', 'lint_issues', 'opinion_versions', 'opinions', 'points', 'relevant_facts', 'range_notes', 'witness_claims', 'witness_comparisons', 'citations', 'report_paragraphs', 'report_builds', 'report_section_texts', 'final_check_runs', 'report_snapshots', 'report_servings', 'spot_checks', 'credit_notes', 'invoices', 'time_estimates', 'time_entries', 'report_settings', 'jobs', 'fact_flags', 'fact_verifications', 'page_reads', 'facts', 'ocr_words', 'page_analysis', 'page_embeddings', 'page_quarantines', 'page_replacements', 'missing_items', 'record_gaps', 'document_parts', 'document_scans', 'exhibits', 'uploads', 'pages', 'questions', 'instruction_versions', 'documents', 'instructing_party_requests', 'case_processing', 'report_versions', 'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_parties', 'case_instructing_parties', 'case_links', 'case_subject_keys', 'case_withdrawals', 'conflict_answer_history', 'conflict_answers', 'conflict_declarations', 'case_members', 'legal_holds', 'case_keys', 'cases']
$$;

reset role;
