-- Down 0805: the sweep lists `export_case` jobs only, as 0802 made it.
set role app_migrator;

create or replace function app.close_stale_exports(p_run_date date, p_days integer, p_window integer)
  returns table (case_id uuid, job_id uuid)
  language sql stable
  security definer
  set search_path = ''
as $$
  select j.case_id, j.id from app.jobs j
  where j.kind = 'export_case' and j.finished_at is not null
    and j.finished_at::date <= p_run_date - p_days
    and j.finished_at::date > p_run_date - p_days - p_window
  order by j.finished_at, j.id
$$;


reset role;
