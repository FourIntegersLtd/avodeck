-- 0072 foundation: one case's backlog waits behind other cases' work (audit 15, BG-11, the ordering half).
--
-- The queue is first in, first out within a priority, so a case with many jobs queued delayed every other case's
-- ordinary job until it drained. A before-insert trigger on the queue table now lowers a new row's priority by how many
-- rows the same case already has `todo` or `doing` on the same queue, at most 9:
--
--   * Bounded: a bulk row (base 0) falls to -9 at the lowest and an interactive row (base 10) to 1, so interactive
--     work stays above bulk work and a kind's priority, pinned by `app.enqueue_job` (BG-09), still decides which
--     side of that line a row is on. `app.jobs.priority` keeps the base value; only the queue row is lowered.
--   * Only the two base priorities (0 and 10). A row the queue code gives another priority (the parent's wake at 20)
--     is its own choice and is left alone.
--   * A worker inside its own job is exempt (`app.job_case_id` is that case): a bundle's page-batch children and the
--     steps that queue the next step are the same case by design, and lowering them would only let other cases'
--     later work overtake a bundle already under way (ADR 0038 relies on them flowing). A person's request, and the
--     heal sweep, are not exempt.
--   * It is a trigger, not a change inside `enqueue_job`, because the queue rows are also made by the retry function,
--     the orphan sweep and the stall requeue; one place covers them all.
--
-- The count is capped by `limit 9` and served by a partial index on the case id.

set role app_migrator;

create index if not exists procrastinate_jobs_case_live_idx
  on procrastinate.procrastinate_jobs ((args ->> 'case_id'), queue_name)
  where status in ('todo', 'doing');

create function app.queue_fairness_priority() returns trigger
  language plpgsql
  security definer
  set search_path = pg_catalog
as $$
declare
  v_case text := new.args ->> 'case_id';
  v_depth integer;
begin
  if new.priority not in (0, 10) or v_case is null
     or v_case = coalesce(app.current_job_case_id()::text, '') then
    return new;
  end if;
  select count(*) into v_depth from (
    select 1 from procrastinate.procrastinate_jobs j
     where j.args ->> 'case_id' = v_case and j.queue_name = new.queue_name and j.status in ('todo', 'doing')
     limit 9
  ) live;
  new.priority := new.priority - v_depth;
  return new;
end
$$;

revoke execute on function app.queue_fairness_priority() from public;

create trigger procrastinate_jobs_fairness_priority
  before insert on procrastinate.procrastinate_jobs
  for each row execute function app.queue_fairness_priority();

reset role;
