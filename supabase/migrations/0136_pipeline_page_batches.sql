-- 0136 pipeline: page-batch runs (ADR 0038, scalability PR 17). Numbers, ids and one date only (rule 6).
--
-- * `pipeline_runs`: one row per parent bundle job that was cut into page batches. `state` follows
--   docs/spec/state-machines.md "Pipeline run". `child_queue` is the queue the children run on, so the
--   SQL that tops the window up knows where to defer them.
-- * `pipeline_batches`: one row per batch of about 100 pages, per phase (`ocr`, then `sort`). `child_job_id`
--   is null until the child is queued and is set once.
-- * The worker reads and writes a case's rows inside a job on that case; the API role has no access at all.
-- * `pipeline_batches_purge_lock`: a case purge (`purge_case_rows`) empties `pipeline_batches` before
--   `pipeline_runs`, but the global lock order is run row first. When `app.purging_case_id` is set, a
--   statement trigger locks the case's run rows `for update` before any batch row is deleted, so a purge
--   cannot deadlock with a child that completes (F64).
-- * `read_page_batch` is queued under the `case.upload` action, like the bundle job.
-- * Both tables are already first in the purge order (foundation 9004) and in `case_tables.py`.

set role app_migrator;

create table app.pipeline_runs (
  job_id uuid primary key references app.jobs (id) on delete cascade,
  case_id uuid not null references app.cases (id) on delete cascade,
  state text not null default 'ocr_waiting'
    check (state in ('ocr_waiting', 'ocr_ready', 'sort_waiting', 'sort_ready', 'finishing', 'done', 'closed')),
  batch_pages integer not null check (batch_pages > 0),
  "window" integer not null check ("window" > 0),
  child_queue text not null default 'bulk' check (child_queue ~ '^[a-z][a-z0-9_]{0,39}$'),
  date_anchor date,
  planned_at timestamptz not null default now(),
  ready_at timestamptz,
  reopened_at timestamptz,
  finished_at timestamptz,
  closed_at timestamptz,
  closed_by_job_id uuid references app.jobs (id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (job_id, case_id)
);
create index pipeline_runs_open on app.pipeline_runs (case_id) where state not in ('done', 'closed');

create table app.pipeline_batches (
  id uuid primary key default gen_random_uuid(),
  run_job_id uuid not null,
  case_id uuid not null references app.cases (id) on delete cascade,
  phase text not null check (phase in ('ocr', 'sort')),
  seq integer not null check (seq >= 0),
  first_page_no integer not null check (first_page_no > 0),
  last_page_no integer not null,
  page_count integer not null check (page_count > 0),
  child_job_id uuid references app.jobs (id) on delete set null,
  completed_at timestamptz,
  created_at timestamptz not null default now(),
  check (last_page_no >= first_page_no),
  unique (run_job_id, phase, seq),
  foreign key (run_job_id, case_id) references app.pipeline_runs (job_id, case_id) on delete cascade
);
create index pipeline_batches_child on app.pipeline_batches (child_job_id) where child_job_id is not null;
create index pipeline_batches_case on app.pipeline_batches (case_id);

create or replace function app.pipeline_batch_child_once() returns trigger
  language plpgsql
  set search_path = ''
as $$
begin
  if old.child_job_id is not null and new.child_job_id is distinct from old.child_job_id then
    raise exception 'a page batch keeps the child job it was given' using errcode = 'P0001';
  end if;
  return new;
end
$$;

create trigger pipeline_batches_child_once before update on app.pipeline_batches
  for each row execute function app.pipeline_batch_child_once();

create or replace function app.pipeline_batches_purge_lock() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_case text := nullif(current_setting('app.purging_case_id', true), '');
begin
  if v_case is not null then
    perform 1 from app.pipeline_runs where case_id = v_case::uuid order by job_id for update;
  end if;
  return null;
end
$$;
revoke execute on function app.pipeline_batches_purge_lock() from public, app_api, app_worker;

create trigger pipeline_batches_purge_lock before delete on app.pipeline_batches
  for each statement execute function app.pipeline_batches_purge_lock();

alter table app.pipeline_runs enable row level security;
alter table app.pipeline_batches enable row level security;
revoke all on app.pipeline_runs, app.pipeline_batches from app_api;
grant select, insert, update on app.pipeline_runs, app.pipeline_batches to app_worker;
revoke delete, truncate on app.pipeline_runs, app.pipeline_batches from app_worker;

create policy pipeline_runs_job_view on app.pipeline_runs for select to app_worker
  using (case_id = app.current_job_case_id());
create policy pipeline_runs_job_write on app.pipeline_runs for insert to app_worker
  with check (case_id = app.current_job_case_id());
create policy pipeline_runs_job_update on app.pipeline_runs for update to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

create policy pipeline_batches_job_view on app.pipeline_batches for select to app_worker
  using (case_id = app.current_job_case_id());
create policy pipeline_batches_job_write on app.pipeline_batches for insert to app_worker
  with check (case_id = app.current_job_case_id());
create policy pipeline_batches_job_update on app.pipeline_batches for update to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

insert into app.job_kind_actions (kind, action) values
  ('read_page_batch', 'case.upload');

reset role;
