-- 0133 pipeline: "Try again" hands a paused retry job back to the queue (state-machines "Job": paused -> running).
--
-- A pause that is not resumable (the cost guard, the key service unreachable) never resumes by itself, so the
-- person who reads the pages needs a way out. FastAPI cannot update `jobs`, so the one allowed change is this
-- function, which does what a support resume does (migration 0035) for the person who may read the case's
-- pages: a paused job of a case not on hold is marked `resuming` (its status stays `paused`; the worker makes
-- the paused -> running move when it picks the job up and clears the code) and its waiting queue row is brought
-- forward, else one new row is deferred with the job's lock. Never a second job, never a second waiting queue
-- row: a repeat press while the job waits for the worker finds nothing to do. Any other status is left alone.
-- Returns whether the job was handed back by this call.

set role app_migrator;

create function app.resume_paused_job(p_job uuid, p_queue text, p_priority integer, p_changes_case boolean)
  returns boolean
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  j app.jobs;
  v_waiting bigint;
  v_lock text;
  v_seen boolean;
begin
  select * into j from app.jobs where id = p_job for update;
  if not found or j.case_id is null or not coalesce(app.can_access_case(j.case_id, 'page.read'), false) then
    return false;
  end if;
  if j.status <> 'paused' then
    return false;
  end if;
  if exists (select 1 from app.cases c where c.id = j.case_id and (c.on_hold or c.status = 'deleted')) then
    return false;
  end if;
  -- Already handed back and still waiting for the worker: nothing more to do.
  if j.error_code = 'resuming' and exists (
    select 1 from procrastinate_jobs pj
     where pj.args ->> 'job_id' = p_job::text and pj.status in ('todo', 'doing')
  ) then
    return false;
  end if;

  update app.jobs set error_code = 'resuming', updated_at = now() where id = p_job;

  select pj.id into v_waiting from procrastinate_jobs pj
   where pj.queue_name = p_queue and pj.args ->> 'job_id' = p_job::text and pj.status = 'todo'
   order by pj.id desc limit 1;
  if v_waiting is not null then
    update procrastinate_jobs set scheduled_at = now() where id = v_waiting;
  else
    select true, pj.lock into v_seen, v_lock from procrastinate_jobs pj
     where pj.args ->> 'job_id' = p_job::text order by pj.id desc limit 1;
    perform procrastinate_defer_jobs_v1(array[row(
      p_queue, 'run_job', p_priority,
      case when p_changes_case then coalesce(case when coalesce(v_seen, false) then v_lock end, 'case:' || j.case_id::text) end,
      null, jsonb_build_object('job_id', p_job, 'case_id', j.case_id), null
    )::procrastinate_job_to_defer_v1]);
  end if;
  return true;
end
$$;

revoke execute on function app.resume_paused_job(uuid, text, integer, boolean) from public, app_api, app_worker;
grant execute on function app.resume_paused_job(uuid, text, integer, boolean) to app_api;

reset role;
