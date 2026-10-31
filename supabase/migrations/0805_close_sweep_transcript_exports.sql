-- 0805 close: the daily sweep of old exports also lists finished `export_transcript` jobs (0215), so a
-- transcript copy nobody downloaded in time is removed (EXPORT_KEEP_DAYS; its paths are removed too).

set role app_migrator;

create or replace function app.close_stale_exports(p_run_date date, p_days integer, p_window integer)
  returns table (case_id uuid, job_id uuid)
  language sql stable
  security definer
  set search_path = ''
as $$
  select j.case_id, j.id from app.jobs j
  where j.kind in ('export_case', 'export_transcript') and j.finished_at is not null
    and j.finished_at::date <= p_run_date - p_days
    and j.finished_at::date > p_run_date - p_days - p_window
  order by j.finished_at, j.id
$$;


reset role;
