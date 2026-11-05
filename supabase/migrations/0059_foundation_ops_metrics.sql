-- 0059 foundation: queue numbers for the ops metrics endpoint (GET /v1/internal/metrics).
--
-- The API and worker roles cannot read the `procrastinate` schema or other people's `jobs` rows, and must not (ADR 0013).
-- These two functions run as their owner and return numbers only, never a job's case, person or text:
--   * `ops_queue_stats()`: per queue, how many jobs are due to run and how long the oldest has waited.
--   * `ops_job_seconds()`: per job kind, the 95th percentile of run time over the last hour.
-- Nothing else is exposed, so RLS is not widened (rule 9).

set role app_migrator;

create function app.ops_queue_stats()
  returns table (queue_name text, queued integer, oldest_seconds double precision)
  language sql stable
  security definer
  set search_path = ''
as $$
  with due as (
    select j.queue_name::text as queue_name,
           coalesce(j.scheduled_at, (select max(e.at) from procrastinate.procrastinate_events e where e.job_id = j.id), now()) as due_at
    from procrastinate.procrastinate_jobs j
    where j.status = 'todo'
  )
  select d.queue_name,
         count(*)::integer,
         coalesce(extract(epoch from now() - min(d.due_at)), 0)::double precision
  from due d
  where d.due_at <= now()
  group by d.queue_name
$$;

create function app.ops_job_seconds()
  returns table (kind text, p95_seconds double precision)
  language sql stable
  security definer
  set search_path = ''
as $$
  select j.kind,
         percentile_cont(0.95) within group (order by extract(epoch from j.finished_at - j.started_at))::double precision
  from app.jobs j
  where j.status = 'done' and j.started_at is not null and j.finished_at > now() - interval '1 hour'
  group by j.kind
$$;

revoke all on function app.ops_queue_stats() from public;
revoke all on function app.ops_job_seconds() from public;
grant execute on function app.ops_queue_stats() to app_api, app_worker;
grant execute on function app.ops_job_seconds() to app_api, app_worker;

reset role;
