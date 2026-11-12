-- 0144 down: back to the 0120 function (upload action for every kind, attempts reset without a cap).

set role app_migrator;

create or replace function app.requeue_failed_job(p_job uuid, p_queue text, p_priority integer, p_changes_case boolean)
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

drop function if exists app.pipeline_retry_limited(uuid);
drop function if exists app.pipeline_retry_action(text);
drop function if exists app.pipeline_retry_cap();
drop table if exists app.pipeline_job_retries;

reset role;
