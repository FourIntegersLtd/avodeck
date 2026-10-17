-- Down for 0015 foundation: job kind actions (the caller names the action again, as in 0007).
set role app_migrator;
drop function if exists app.enqueue_job(uuid, text, text, integer, boolean, text);
drop table if exists app.job_kind_actions;
create function app.enqueue_job(
  p_case_id uuid, p_kind text, p_queue text, p_priority integer, p_changes_case boolean,
  p_action text, p_code_version text
) returns uuid
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_id uuid;
begin
  if not (coalesce(app.current_job_case_id() = p_case_id, false)
          or coalesce(app.can_access_case(p_case_id, p_action), false)) then
    raise exception using errcode = '42501', message = 'no access to this case';
  end if;
  insert into app.jobs (case_id, kind, priority, code_version)
  values (p_case_id, p_kind, p_priority, p_code_version)
  returning id into v_id;
  perform procrastinate_defer_jobs_v1(array[row(
    p_queue, 'run_job', p_priority,
    case when p_changes_case then 'case:' || p_case_id::text end,
    null, jsonb_build_object('job_id', v_id, 'case_id', p_case_id), null
  )::procrastinate_job_to_defer_v1]);
  return v_id;
end
$$;

revoke execute on function app.enqueue_job(uuid, text, text, integer, boolean, text, text) from public, app_api, app_worker;
grant execute on function app.enqueue_job(uuid, text, text, integer, boolean, text, text) to app_api, app_worker;
reset role;
