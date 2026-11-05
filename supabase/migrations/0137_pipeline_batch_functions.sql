-- 0137 pipeline: the SQL that runs a page-batch run (ADR 0038, scalability PR 17).
--
-- All functions are security definer and the worker role may call them; the API role and public may not.
-- Global lock order, everywhere: the case lock `case:<id>` (procrastinate) -> `pipeline_runs` row -> page rows ->
-- `pipeline_batches` rows -> `jobs` rows -> queue rows. No function takes a `cases` row lock while it holds a
-- run row. `pipeline_redefer` and `pipeline_defer_row` use `procrastinate, pg_catalog` as their search path so
-- `procrastinate_defer_jobs_v1` resolves (like 0022 and 9001); the rest use an empty search path.
-- Counts, ids and codes only: nothing here reads page text (rule 6).

set role app_migrator;

-- One new queue row for an EXISTING job. No grant to any role: only definer functions below call it.
create or replace function app.pipeline_defer_row(p_job uuid, p_queue text, p_lock text, p_priority integer)
  returns void
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_case uuid;
begin
  select case_id into v_case from app.jobs where id = p_job;
  perform procrastinate_defer_jobs_v1(array[row(
    p_queue, 'run_job', p_priority, p_lock,
    null, jsonb_build_object('job_id', p_job, 'case_id', v_case), null
  )::procrastinate_job_to_defer_v1]);
end
$$;
revoke execute on function app.pipeline_defer_row(uuid, text, text, integer) from public, app_api, app_worker;

-- A wake for a running parent (p_wake) or the hand-back of a paused waiter. Returns false, changing nothing, for a
-- job or case that is gone or deleted, a held case, a job in the wrong status, or a job that already has a waiting or
-- running queue row (so two calls make one row).
create or replace function app.pipeline_redefer(p_job uuid, p_wake boolean default false)
  returns boolean
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  j app.jobs;
  v_queue text;
  v_lock text;
  v_found boolean;
begin
  select * into j from app.jobs where id = p_job for update;
  if not found or j.case_id is null then
    return false;
  end if;
  if (p_wake and j.status <> 'running') or (not p_wake and j.status <> 'paused') then
    return false;
  end if;
  if exists (select 1 from app.cases c where c.id = j.case_id and (c.on_hold or c.status = 'deleted')) then
    return false;
  end if;
  if exists (
    select 1 from procrastinate_jobs pj where pj.args ->> 'job_id' = p_job::text and pj.status in ('todo', 'doing')
  ) then
    return false;
  end if;
  select pj.queue_name, pj.lock, true into v_queue, v_lock, v_found from procrastinate_jobs pj
   where pj.args ->> 'job_id' = p_job::text order by pj.id desc limit 1;
  if not coalesce(v_found, false) then
    v_queue := 'bulk';
    v_lock := 'case:' || j.case_id::text;
  end if;
  perform app.pipeline_defer_row(p_job, v_queue, v_lock, case when p_wake then 20 else j.priority end);
  if p_wake then
    update app.jobs set requeued_stall = true where id = p_job;
  end if;
  return true;
end
$$;
revoke execute on function app.pipeline_redefer(uuid, boolean) from public, app_api, app_worker;
grant execute on function app.pipeline_redefer(uuid, boolean) to app_worker;

-- True while the case's bundle work is going on, so the jobs that used the lock to wait for the bundle keep waiting:
-- an open run with a running or queued parent, a child with a waiting or running queue row, or a parent paused with
-- a code the caller names as resumable. A run left open by a Stop, a cost-guard pause or a failure is not busy.
create or replace function app.pipeline_case_busy(p_case uuid, p_resumable text[])
  returns boolean
  language sql
  stable
  security definer
  set search_path = procrastinate, pg_catalog
as $$
  select exists (
    select 1
      from app.pipeline_runs r
      join app.jobs p on p.id = r.job_id
     where r.case_id = p_case
       and r.state not in ('done', 'closed')
       and (
         p.status in ('running', 'queued')
         or (p.status = 'paused' and p.error_code = any (p_resumable))
         or exists (
           select 1
             from app.pipeline_batches b
             join procrastinate_jobs pj on pj.args ->> 'job_id' = b.child_job_id::text
            where b.run_job_id = r.job_id and pj.status in ('todo', 'doing')
         )
       )
  )
$$;
revoke execute on function app.pipeline_case_busy(uuid, text[]) from public, app_api, app_worker;
grant execute on function app.pipeline_case_busy(uuid, text[]) to app_worker;

reset role;
