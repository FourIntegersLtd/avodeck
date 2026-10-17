-- 0113 pipeline: one embedding per page for search (architecture 6.1 M7).
--
-- * `page_embeddings`: the vector of a page's text after identifiers are stripped (rule 7), with the
--   embedder that made it. A page embedded by an older model is embedded again; search uses rows of
--   the current model only. The worker writes; people may read through the case's view access.
-- * The table joins the purge order (= PURGE_ORDER in api/app/core/contracts/case_tables.py).

create extension if not exists vector with schema extensions;
grant usage on schema extensions to app_migrator, app_api, app_worker;

set role app_migrator;

create table app.page_embeddings (
  page_id uuid primary key references app.pages (id) on delete cascade,
  case_id uuid not null references app.cases (id) on delete cascade,
  model text not null check (model ~ '^[a-z0-9][a-z0-9._:-]{0,99}$'),
  embedding extensions.vector(384) not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index page_embeddings_case on app.page_embeddings (case_id, model);

create trigger page_embeddings_same_case before insert or update on app.page_embeddings
  for each row execute function app.pipeline_same_case_page();

alter table app.page_embeddings enable row level security;
grant select on app.page_embeddings to app_api;
grant select, insert, update on app.page_embeddings to app_worker;
revoke insert, update, delete, truncate on app.page_embeddings from app_api;
revoke delete, truncate on app.page_embeddings from app_worker;

create policy page_embeddings_view on app.page_embeddings for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_embeddings_job_view on app.page_embeddings for select to app_worker
  using (case_id = app.current_job_case_id());
create policy page_embeddings_job_write on app.page_embeddings for insert to app_worker
  with check (case_id = app.current_job_case_id());
create policy page_embeddings_job_update on app.page_embeddings for update to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create or replace function app.case_purge_order() returns text[]
  language sql immutable parallel safe
  set search_path = ''
as $$
  select array['job_steps', 'jobs', 'facts', 'ocr_words', 'page_analysis', 'page_embeddings', 'page_quarantines',
               'page_replacements', 'missing_items', 'record_gaps', 'document_parts', 'document_scans', 'exhibits',
               'uploads', 'pages', 'documents', 'instructing_party_requests', 'case_processing', 'report_versions',
               'questions', 'transcripts', 'ai_calls', 'notifications', 'idempotency_keys', 'case_members',
               'legal_holds', 'case_keys', 'cases']
$$;

reset role;
