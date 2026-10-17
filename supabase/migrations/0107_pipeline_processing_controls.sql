-- 0107 pipeline: the expert's controls over processing a case's bundle (screen 07).
--
-- One row per case. `cancel_requested_at` stops the bundle jobs queued or running at that moment at
-- the next page boundary (pages being read finish; jobs queued later are not affected).
-- `reprocess_opted_at` is the opt-in to read every page again with the cell's current model: pages
-- extracted before it are extracted again by the `reprocess_case` job; verified facts never change.

set role app_migrator;

create table app.case_processing (
  case_id uuid primary key references app.cases (id) on delete cascade,
  cancel_requested_at timestamptz,
  cancel_requested_by uuid references app.people (id),
  reprocess_opted_at timestamptz,
  reprocess_opted_by uuid references app.people (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table app.case_processing enable row level security;
grant select, insert, update on app.case_processing to app_api;
grant select on app.case_processing to app_worker;
revoke delete, truncate on app.case_processing from app_api, app_worker;

create policy case_processing_view on app.case_processing for select to app_api
  using (app.can_access_case(case_id, 'case.view_metadata'));
create policy case_processing_add on app.case_processing for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy case_processing_change on app.case_processing for update to app_api
  using (app.can_access_case(case_id, 'case.upload')) with check (app.can_access_case(case_id, 'case.upload'));
create policy case_processing_job on app.case_processing for select to app_worker
  using (case_id = app.current_job_case_id());

reset role;
