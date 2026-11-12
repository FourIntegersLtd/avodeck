-- 0145 pipeline: whether a bundle job has an open page-batch run, readable by the API role (BG-08, ADR 0038).
--
-- `POST /process` returns the case's live bundle job instead of queueing another. A job whose page-batch run is open
-- has planned its pages already, so a start made now (an append, or a start after a Stop the sweep has not mirrored)
-- needs a job of its own. The API role has no access to `pipeline_runs`, so the one fact it needs comes through this
-- function: a boolean, for a job of a case the caller may see, and nothing else.

set role app_migrator;

create function app.pipeline_run_open(p_job uuid)
  returns boolean
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select exists (
    select 1
      from app.pipeline_runs r
      join app.jobs j on j.id = r.job_id
     where r.job_id = p_job
       and r.state not in ('done', 'closed')
       and (coalesce(app.can_access_case(j.case_id, 'page.read'), false) or j.case_id = app.current_job_case_id())
  )
$$;

revoke execute on function app.pipeline_run_open(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_run_open(uuid) to app_api, app_worker;

reset role;
