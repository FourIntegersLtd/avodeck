-- 9001 down: the lookup by queue name again.

set role app_migrator;

create or replace function app.resume_paused_job(p_job uuid, p_queue text default 'jobs')
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

reset role;
