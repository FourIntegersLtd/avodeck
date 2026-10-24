-- 0120 pipeline: "Try again" puts a failed processing job back on the queue (state-machines "Job": failed -> queued).
--
-- FastAPI cannot update `jobs` (only the worker can), so the one allowed change is this function: it checks
-- the person may upload to the case (the action that starts processing), moves a `failed` job to `queued`
-- with attempts counted again from none, and defers it to the queue like `app.enqueue_job()` does. Any
-- other status is left alone, so a repeat finds nothing to do.

set role app_migrator;

create function app.requeue_failed_job(p_job uuid, p_queue text, p_priority integer, p_changes_case boolean)
  returns boolean
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_case uuid;
begin
  select j.case_id into v_case from app.jobs j where j.id = p_job;
  if v_case is null or not coalesce(app.can_access_case(v_case, 'case.upload'), false) then
    return false;
  end if;
  update app.jobs set status = 'queued', error_code = null, attempts = 0, step = null,
    finished_at = null, updated_at = now()
  where id = p_job and status = 'failed';
  if not found then
    return false;
  end if;
  perform procrastinate_defer_jobs_v1(array[row(
    p_queue, 'run_job', p_priority,
    case when p_changes_case then 'case:' || v_case::text end,
    null, jsonb_build_object('job_id', p_job, 'case_id', v_case), null
  )::procrastinate_job_to_defer_v1]);
  return true;
end
$$;

revoke execute on function app.requeue_failed_job(uuid, text, integer, boolean) from public, app_api, app_worker;
grant execute on function app.requeue_failed_job(uuid, text, integer, boolean) to app_api;

reset role;
