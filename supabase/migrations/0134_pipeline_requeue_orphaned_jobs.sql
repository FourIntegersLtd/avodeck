-- 0134 pipeline: a `running` job whose queue row has ended is put back (state-machines "Job").
--
-- When a worker's database connection dropped while it recorded that a job had failed (or finished), the
-- queue library ended the queue row but the job stayed `running` in `app.jobs`, so the screen spun for ever.
-- The worker calls this at start and on every housekeeping round: a job `running` and last changed more than
-- `p_grace_seconds` ago, with no queue row waiting for or held by a worker (todo or doing), is moved
-- running -> failed (`job_error`), and, while it has attempts left (`attempts` counts this run), failed ->
-- queued with one new queue row deferred for it on the queue its earlier row was on (else `jobs`), with the
-- job's own priority and the lock of that earlier row (else `case:<id>`), like 0133. A job out of attempts stays
-- `failed`. Both moves are the guard's allowed ones. Nothing is passed in but the limits, so the caller chooses
-- no queue, priority or lock. Returns how many jobs were put right. Granted to the worker only.

set role app_migrator;

create function app.requeue_orphaned_jobs(p_max_attempts integer, p_grace_seconds double precision)
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
     where aj.status = 'running'
       and aj.updated_at < now() - make_interval(secs => p_grace_seconds)
       and not exists (
         select 1 from procrastinate_jobs pj
          where pj.args ->> 'job_id' = aj.id::text and pj.status in ('todo', 'doing')
       )
     order by aj.updated_at
     for update of aj skip locked
  loop
    update app.jobs set status = 'failed', error_code = 'job_error', finished_at = now() where id = j.id;
    if j.attempts < p_max_attempts then
      update app.jobs set status = 'queued', finished_at = null where id = j.id;
      v_lock := null;
      v_queue := null;
      select pj.queue_name into v_queue from procrastinate_jobs pj
       where pj.args ->> 'job_id' = j.id::text order by pj.id desc limit 1;
      select pj.lock into v_lock from procrastinate_jobs pj
       where pj.args ->> 'job_id' = j.id::text and pj.lock is not null order by pj.id desc limit 1;
      perform procrastinate_defer_jobs_v1(array[row(
        coalesce(v_queue, 'jobs'), 'run_job', j.priority, coalesce(v_lock, 'case:' || j.case_id::text),
        null, jsonb_build_object('job_id', j.id, 'case_id', j.case_id), null
      )::procrastinate_job_to_defer_v1]);
    end if;
    v_count := v_count + 1;
  end loop;
  return v_count;
end
$$;

revoke execute on function app.requeue_orphaned_jobs(integer, double precision) from public, app_api, app_worker;
grant execute on function app.requeue_orphaned_jobs(integer, double precision) to app_worker;

reset role;
