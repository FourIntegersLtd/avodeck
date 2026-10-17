-- 0015 foundation: the database decides which case action a job kind needs (rule 9).
-- `app.enqueue_job()` took the action from its caller; now it looks the kind up here, so nobody
-- can queue an export or a deletion with the action of an upload. Kinds not listed need
-- `case.upload`. A slice adding a kind that needs more asks the foundation owner for a row.

set role app_migrator;

create table app.job_kind_actions (
  kind text primary key check (kind ~ '^[a-z][a-z0-9_]{0,39}$'),
  action text not null
);
insert into app.job_kind_actions (kind, action) values
  ('process_bundle', 'case.upload'),
  ('process_pages', 'case.upload'),
  ('extract_questions', 'case.upload'),
  ('verify_opposing', 'case.upload'),
  ('match_trial_bundle', 'case.upload'),
  ('rerun_checks', 'page.read'),
  ('build_report', 'report.build'),
  ('final_check', 'report.build'),
  ('build_trial_pack', 'report.build'),
  ('export_case', 'case.export'),
  ('delete_case', 'case.delete_now');

alter table app.job_kind_actions enable row level security;
revoke insert, update, delete on app.job_kind_actions from app_api, app_worker;
create policy job_kind_actions_read on app.job_kind_actions for select to app_api, app_worker using (true);

drop function app.enqueue_job(uuid, text, text, integer, boolean, text, text);

create function app.enqueue_job(
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

revoke execute on function app.enqueue_job(uuid, text, text, integer, boolean, text) from public, app_api, app_worker;
grant execute on function app.enqueue_job(uuid, text, text, integer, boolean, text) to app_api, app_worker;

reset role;
