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
--
-- The caller chooses nothing but the job: the priority is the job's own, the queue is the one jobs run on
-- (`jobs`; any other name is refused), and the queue row is always locked like 0035's: the lock of the job's
-- earlier queue row, else `case:<id>`, so no second job of the case runs beside it.
--
-- app.job_waits_in_queue(job) tells the summary whether a handed-back job still has a queue row waiting for or
-- held by a worker (todo or doing): `resuming` with none (a hold was placed before the worker picked it up, so the
-- worker ended the row without running the job) is not running, and Try again must come back.

set role app_migrator;

create function app.resume_paused_job(p_job uuid, p_queue text default 'jobs')
  returns boolean
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  j app.jobs;
  v_waiting bigint;
  v_lock text;
begin
  if p_queue is distinct from 'jobs' then
    raise exception using errcode = '22023', message = 'unknown job queue';
  end if;
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
    select pj.lock into v_lock from procrastinate_jobs pj
     where pj.args ->> 'job_id' = p_job::text and pj.lock is not null order by pj.id desc limit 1;
    perform procrastinate_defer_jobs_v1(array[row(
      p_queue, 'run_job', j.priority, coalesce(v_lock, 'case:' || j.case_id::text),
      null, jsonb_build_object('job_id', p_job, 'case_id', j.case_id), null
    )::procrastinate_job_to_defer_v1]);
  end if;
  return true;
end
$$;

create function app.job_waits_in_queue(p_job uuid)
  returns boolean
  language sql
  stable
  security definer
  set search_path = procrastinate, pg_catalog
as $$
  select exists (
    select 1 from app.jobs j
     where j.id = p_job and coalesce(app.can_access_case(j.case_id, 'page.read'), false)
       and exists (
         select 1 from procrastinate_jobs pj
          where pj.args ->> 'job_id' = p_job::text and pj.status in ('todo', 'doing')
       )
  )
$$;

revoke execute on function app.resume_paused_job(uuid, text), app.job_waits_in_queue(uuid) from public, app_api, app_worker;
grant execute on function app.resume_paused_job(uuid, text), app.job_waits_in_queue(uuid) to app_api;

reset role;
