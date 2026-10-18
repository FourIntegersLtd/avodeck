-- 0204 reader: fact flags (`fact_flags`). The named expert marks a fact while reading so a later
-- screen shows it first (`flagged_for = 'points'`: screen 14, slice E reads it through
-- core/contracts/fact_flags.py). One flag per fact and purpose; removing a flag deletes its row.

set role app_migrator;

create table app.fact_flags (
  id uuid primary key default gen_random_uuid(),
  case_id uuid not null references app.cases (id) on delete cascade,
  fact_id uuid not null references app.facts (id) on delete cascade,
  flagged_for text not null check (flagged_for ~ '^[a-z][a-z0-9_]{0,39}$'),
  person_id uuid not null references app.people (id),
  at timestamptz not null default now(),
  unique (fact_id, flagged_for)
);
create index fact_flags_case on app.fact_flags (case_id, flagged_for);

alter table app.fact_flags enable row level security;
grant select, insert, delete on app.fact_flags to app_api;
grant select on app.fact_flags to app_worker;

create policy fact_flags_view on app.fact_flags for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy fact_flags_add on app.fact_flags for insert to app_api
  with check (app.can_access_case(case_id, 'fact.verify') and person_id = app.current_person_id()
    and exists (select 1 from app.facts f where f.id = fact_id and f.case_id = fact_flags.case_id));
create policy fact_flags_remove on app.fact_flags for delete to app_api
  using (app.can_access_case(case_id, 'fact.verify'));
create policy fact_flags_job on app.fact_flags for select to app_worker
  using (case_id = app.current_job_case_id());

reset role;
