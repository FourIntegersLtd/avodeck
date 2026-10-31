-- 0121 pipeline: where one entry in a record starts and ends on a page (ADR 0028, architecture 4.1).
--
-- * `record_entries`: one row per entry T-07b found on a page: its kind, the numbers of its first and last
--   word (`ocr_words.n`), who wrote it and its specialty (pack keys), and the date printed inside it. It
--   holds no page text: the transcript is laid out from the words themselves, at read time.
-- * `page_analysis.entries_extracted_at` says the page's entries have been read (a page with none still
--   counts), and `entries_rejected` how many entries failed provenance and were dropped.
-- * job kind `extract_entries`: reads entries for a case that has none (one extracted before entries
--   existed). Status is polled through the contract `core/contracts/record_entries.py`.
--
-- The API sees rows with `case.view_content`; the worker only inside a job on the same case. The purge
-- order is spliced in by migration 1015, which must run after every slice's own replacement.

set role app_migrator;

alter table app.page_analysis
  add column entries_extracted_at timestamptz,
  add column entries_rejected integer not null default 0 check (entries_rejected >= 0);

create table app.record_entries (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references app.pages (id) on delete cascade,
  case_id uuid not null references app.cases (id) on delete cascade,
  kind text not null check (kind ~ '^[a-z][a-z0-9_]{0,59}$'),
  first_n integer not null check (first_n > 0),
  last_n integer not null,
  author_role text check (author_role ~ '^[a-z][a-z0-9_]{0,59}$'),
  specialty text check (specialty ~ '^[a-z][a-z0-9_]{0,59}$'),
  event_date date,
  date_basis text check (date_basis ~ '^[a-z][a-z0-9_]{0,39}$'),
  extraction_version text not null check (extraction_version ~ '^[A-Za-z0-9_.\-]{1,40}$'),
  created_at timestamptz not null default now(),
  check (first_n <= last_n)
);
create index record_entries_case_date on app.record_entries (case_id, event_date nulls last);
create index record_entries_page on app.record_entries (page_id, first_n);

create trigger record_entries_same_case before insert or update on app.record_entries
  for each row execute function app.pipeline_same_case_page();

alter table app.record_entries enable row level security;
revoke insert, update, delete on app.record_entries from app_api;

create policy record_entries_view on app.record_entries for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy record_entries_job on app.record_entries to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

insert into app.job_kind_actions (kind, action) values
  ('extract_entries', 'page.read');

reset role;
