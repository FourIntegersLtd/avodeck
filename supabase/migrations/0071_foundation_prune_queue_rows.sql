-- 0071 foundation: finished queue rows are pruned (audit 15, BG-14; closes the queue half of audit 12 D1f).
--
-- Nothing ever deleted a finished `procrastinate_jobs` row or its events, so both tables only grew (a cost and a
-- slower sweep; ids and times only, no patient text). `app.prune_queue_rows(days, batch)` deletes up to `batch`
-- finished rows (succeeded, failed, cancelled, aborted) whose latest event is older than `days`, and any finished row
-- of a deleted case at once. Events go with their row (cascade). The worker's housekeeping calls it once a day.
-- Security definer so the worker role needs no delete right on the queue schema (it still has one: BG-10).

set role app_migrator;

create function app.prune_queue_rows(p_older_than_days integer, p_batch integer default 5000)
  returns integer
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  n integer;
begin
  if p_older_than_days is null or p_older_than_days < 0 or p_batch is null or p_batch < 1 then
    raise exception using errcode = '22023', message = 'prune_queue_rows needs days >= 0 and a batch >= 1';
  end if;
  with deleted_cases as materialized (
    select c.id::text as id from app.cases c where c.status = 'deleted'
  ), doomed as (
    select j.id from procrastinate_jobs j
     where j.status in ('succeeded', 'failed', 'cancelled', 'aborted')
       and (coalesce((select max(e.at) from procrastinate_events e where e.job_id = j.id), j.scheduled_at)
              < now() - make_interval(days => p_older_than_days)
            or j.args ->> 'case_id' in (select d.id from deleted_cases d))
     limit p_batch
  )
  delete from procrastinate_jobs where id in (select id from doomed);
  get diagnostics n = row_count;
  return n;
end
$$;

revoke execute on function app.prune_queue_rows(integer, integer) from public, app_api, app_worker;
grant execute on function app.prune_queue_rows(integer, integer) to app_worker;

reset role;
