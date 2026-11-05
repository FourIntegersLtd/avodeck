-- 0140 down: the purge-lock trigger as 0136 had it (every run of the case, uuid order).

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
    perform 1 from app.pipeline_runs where case_id = v_case::uuid order by job_id for update;
  end if;
  return null;
end
$$;

reset role;
