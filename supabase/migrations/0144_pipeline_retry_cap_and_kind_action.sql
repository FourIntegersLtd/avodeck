-- 0144 pipeline: "Try again" checks the action of the job's own kind and is capped for the job's whole life
-- (audit 15, BG-04 and BG-07). Replaces `app.requeue_failed_job` of 0120 (that file stays as applied).
--
-- * BG-04. The function checked `case.upload` for every kind. It now refuses a kind the retry route does not put
--   back (only the four processing kinds), and takes the action from `app.job_kind_actions` for the kind, like
--   `app.enqueue_job` does. A kind with no row is refused.
-- * BG-07. Each retry reset `attempts` to 0 without end. A person's retries of one job are now counted in
--   `app.pipeline_job_retries` (one row per job, gone with the job) and the 6th is refused: the function answers
--   False, as for any job that cannot be put back, and `app.pipeline_retry_limited(job)` says why, so the route
--   answers 409 `retry_limit`. Support's requeue (`app.support_job_action`) does not use this table and is not capped.
-- * The table holds a job id and a number. The API and worker roles have no access to it: only these functions.

set role app_migrator;

create table app.pipeline_job_retries (
  job_id uuid primary key references app.jobs (id) on delete cascade,
  retries integer not null default 0 check (retries >= 0),
  updated_at timestamptz not null default now()
);
alter table app.pipeline_job_retries enable row level security;
revoke all on app.pipeline_job_retries from public, app_api, app_worker;

create function app.pipeline_retry_cap() returns integer language sql immutable set search_path = '' as 'select 5';

-- The action a person needs to put this failed job back: that of its kind, for the kinds the route retries; else null.
create function app.pipeline_retry_action(p_kind text) returns text
  language sql stable set search_path = ''
as $$
  select a.action from app.job_kind_actions a
   where a.kind = p_kind and p_kind in ('scan_document', 'process_bundle', 'process_pages', 'reprocess_case')
$$;

create or replace function app.requeue_failed_job(p_job uuid, p_queue text, p_priority integer, p_changes_case boolean)
  returns boolean
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_case uuid;
  v_kind text;
  v_action text;
  v_used integer;
begin
  select j.case_id, j.kind into v_case, v_kind from app.jobs j where j.id = p_job and j.status = 'failed' for update;
  v_action := app.pipeline_retry_action(v_kind);
  if v_case is null or v_action is null or not coalesce(app.can_access_case(v_case, v_action), false) then
    return false;
  end if;
  select r.retries into v_used from app.pipeline_job_retries r where r.job_id = p_job for update;
  if coalesce(v_used, 0) >= app.pipeline_retry_cap() then
    return false;
  end if;
  update app.jobs set status = 'queued', error_code = null, attempts = 0, step = null,
    finished_at = null, updated_at = now()
  where id = p_job and status = 'failed';
  if not found then
    return false;
  end if;
  insert into app.pipeline_job_retries (job_id, retries) values (p_job, 1)
  on conflict (job_id) do update set retries = app.pipeline_job_retries.retries + 1, updated_at = now();
  perform procrastinate_defer_jobs_v1(array[row(
    p_queue, 'run_job', p_priority,
    case when p_changes_case then 'case:' || v_case::text end,
    null, jsonb_build_object('job_id', p_job, 'case_id', v_case), null
  )::procrastinate_job_to_defer_v1]);
  return true;
end
$$;

-- True when the job is failed, the person may put it back, and it has used all its retries.
create function app.pipeline_retry_limited(p_job uuid) returns boolean
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select coalesce((
    select coalesce(app.can_access_case(j.case_id, app.pipeline_retry_action(j.kind)), false)
       and coalesce((select r.retries from app.pipeline_job_retries r where r.job_id = j.id), 0)
           >= app.pipeline_retry_cap()
      from app.jobs j
     where j.id = p_job and j.status = 'failed'
  ), false)
$$;

revoke execute on function app.pipeline_retry_cap() from public, app_api, app_worker;
revoke execute on function app.pipeline_retry_action(text) from public, app_api, app_worker;
revoke execute on function app.pipeline_retry_limited(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_retry_limited(uuid) to app_api;

reset role;
