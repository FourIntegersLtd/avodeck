-- 0140 pipeline: the case purge locks the case's runs in the fan-in's order (ADR 0038; review of 0136/0137).
--
-- 0136's purge-lock trigger took the case's runs ordered by `job_id` (uuid order). The fan-in functions
-- (`pipeline_lock_runs`, 0137) take the same set in `created_at, job_id` order, so a purge and a completion could each
-- hold one run row and want the other (`40P01`). The trigger now calls `pipeline_lock_runs`: one set (every run of the
-- case, closed ones too), one order.

set role app_migrator;

create or replace function app.pipeline_batches_purge_lock() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
declare
  v_case text := nullif(current_setting('app.purging_case_id', true), '');
begin
  if v_case is not null then
    perform app.pipeline_lock_runs(v_case::uuid);
  end if;
  return null;
end
$$;

reset role;
