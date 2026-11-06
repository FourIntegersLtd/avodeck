-- 0141 pipeline: the time a bundle job's run was last reopened, readable by the API role (ADR 0038, build step 8a).
--
-- A Stop made before "Try again" must not stop the reopened children (the stored stop is older than the reopen), and a
-- second Stop after it must. `repo.request_cancel` (API role) and `repo.cancel_requested` (worker role) compare the
-- stored stop with `greatest(created_at, reopened_at)`. The API role has no access to `pipeline_runs`, so the one value
-- it needs comes through this function: a timestamp, for a job of a case the caller may see, and nothing else.

set role app_migrator;

create function app.pipeline_reopened_at(p_job uuid)
  returns timestamptz
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select r.reopened_at
    from app.pipeline_runs r
    join app.jobs j on j.id = r.job_id
   where r.job_id = p_job
     and (coalesce(app.can_access_case(j.case_id, 'page.read'), false) or j.case_id = app.current_job_case_id())
$$;

revoke execute on function app.pipeline_reopened_at(uuid) from public, app_api, app_worker;
grant execute on function app.pipeline_reopened_at(uuid) to app_api, app_worker;

reset role;
