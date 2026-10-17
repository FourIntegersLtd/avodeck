-- Down for 0112: the purge order as foundation 0017 left it.
set role app_migrator;

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'jobs', 'facts', 'uploads', 'pages', 'documents', 'report_versions', 'questions',
               'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_members', 'legal_holds',
               'case_keys', 'cases']
$$;

reset role;
