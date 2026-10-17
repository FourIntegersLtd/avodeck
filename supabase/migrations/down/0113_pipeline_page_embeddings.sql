-- Down for 0113: page embeddings removed and the purge order as 0112 left it.
set role app_migrator;

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'jobs', 'facts', 'ocr_words', 'page_analysis', 'page_quarantines', 'page_replacements',
               'missing_items', 'record_gaps', 'document_parts', 'document_scans', 'exhibits', 'uploads', 'pages',
               'documents', 'instructing_party_requests', 'case_processing', 'report_versions', 'questions',
               'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_members', 'legal_holds',
               'case_keys', 'cases']
$$;

drop table if exists app.page_embeddings;

reset role;
