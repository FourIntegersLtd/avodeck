set role app_migrator;

create or replace function app.case_report_input_allowed(p_case uuid, p_action text) returns boolean
  language sql
  stable
  security definer
  set search_path = ''
as $$
  select coalesce(app.current_job_case_id() = p_case, false)
         or coalesce(app.can_access_case(p_case, p_action), false)
$$;

reset role;
