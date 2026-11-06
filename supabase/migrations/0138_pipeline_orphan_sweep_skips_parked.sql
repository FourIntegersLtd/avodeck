-- 0138 pipeline: the orphaned-jobs sweep leaves a parked parent alone and puts a page-batch child back with no lock
-- (ADR 0038, F66).
--
-- A batch-mode parent with an open run (`pipeline_runs.state` not `done` or `closed`) is `running` with no queue row of
-- its own while its children read the pages (parked): that is not an orphan, and requeueing it would run it a second time
-- beside its children. It is skipped; the heal sweep (`pipeline_heal_runs`, 0137) wakes it when it is stranded. A job
-- that is a batch child (`app.is_batch_child`, 9003) is requeued with a null lock, never `case:<id>`, so one child cannot
-- block the case's other work. Everything else is as 0135. Grants are those of 0134 (the worker only).

set role app_migrator;

create or replace function app.requeue_orphaned_jobs(p_max_attempts integer, p_grace_seconds double precision)
  returns integer
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  j app.jobs;
  v_lock text;
  v_queue text;
  v_count integer := 0;
begin
  for j in
    select * from app.jobs aj
     where (aj.status = 'running' or (aj.status = 'queued' and aj.error_code is distinct from 'case_on_hold'))
       and aj.updated_at < now() - make_interval(secs => p_grace_seconds)
       and not (aj.status = 'running' and exists (
         select 1 from app.pipeline_runs pr where pr.job_id = aj.id and pr.state not in ('done', 'closed')
       ))
       and not exists (
         select 1 from procrastinate_jobs pj
          where pj.args ->> 'job_id' = aj.id::text and pj.status in ('todo', 'doing')
       )
     order by aj.updated_at
     for update of aj skip locked
  loop
    if j.status = 'running' then
      update app.jobs set status = 'failed', error_code = 'job_error', finished_at = now() where id = j.id;
    end if;
    if j.status = 'queued' or j.attempts < p_max_attempts then
      if j.status = 'running' then
        update app.jobs set status = 'queued', finished_at = null where id = j.id;
      end if;
      v_lock := null;
      v_queue := null;
      select pj.queue_name into v_queue from procrastinate_jobs pj
       where pj.args ->> 'job_id' = j.id::text order by pj.id desc limit 1;
      select pj.lock into v_lock from procrastinate_jobs pj
       where pj.args ->> 'job_id' = j.id::text and pj.lock is not null order by pj.id desc limit 1;
      perform procrastinate_defer_jobs_v1(array[row(
        coalesce(v_queue, 'jobs'), 'run_job', j.priority,
        case when app.is_batch_child(j.id) then null else coalesce(v_lock, 'case:' || j.case_id::text) end,
        null, jsonb_build_object('job_id', j.id, 'case_id', j.case_id), null
      )::procrastinate_job_to_defer_v1]);
    end if;
    v_count := v_count + 1;
  end loop;
  return v_count;
end
$$;



reset role;
