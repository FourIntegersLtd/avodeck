-- Undo 0070: enqueue_job as in 0015 (the caller's queue and priority are trusted again, no cap); drop the columns.
set role app_migrator;

create or replace function app.enqueue_job(
  p_case_id uuid, p_kind text, p_queue text, p_priority integer, p_changes_case boolean, p_code_version text
) returns uuid
  language plpgsql
  security definer
  set search_path = procrastinate, pg_catalog
as $$
declare
  v_id uuid;
  v_action text := coalesce((select a.action from app.job_kind_actions a where a.kind = p_kind), 'case.upload');
begin
  if not (coalesce(app.current_job_case_id() = p_case_id, false)
          or coalesce(app.can_access_case(p_case_id, v_action), false)) then
    raise exception using errcode = '42501', message = 'this job needs ' || v_action;
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

alter table app.job_kind_actions drop column max_active, drop column interactive;

reset role;
