-- 0027 foundation: only the worker role can act as a case's job in the report-input check.
-- `app.job_case_id` is a plain session setting, so the API role could set it itself and read the named
-- expert's template, declarations, signature and conflicts. The check in 0026 now also requires the
-- connecting role to be `app_worker`. The function is security definer, so `current_user` is its owner;
-- `session_user` is the role that connected, which is the one to test.

set role app_migrator;

create or replace function app.case_report_input_allowed(p_case uuid, p_action text) returns boolean
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select coalesce(session_user = 'app_worker' and app.current_job_case_id() = p_case, false)
         or coalesce(app.can_access_case(p_case, p_action), false)
$$;

reset role;
