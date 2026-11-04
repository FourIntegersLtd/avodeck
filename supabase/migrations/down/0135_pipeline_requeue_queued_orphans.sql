-- 0135 down: the sweep as 0134 had it (running jobs only).

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

reset role;
