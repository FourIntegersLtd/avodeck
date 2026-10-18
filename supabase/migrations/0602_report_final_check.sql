-- 0602 report: what slice F's final check needs (screen 17, architecture 4.7).
-- * `final_check_runs.warnings_status`: whether T-17 (tone and expertise) ran (`done`) or could not run
--   this time (`not_run`, the model was unavailable); warnings never block, so the code checks still count.
-- * `app.report_named_expert(case)`: the named expert's name, registration and fee details for the
--   case, for the final check ("expert details complete"), signing and invoices. It reads the foundation
--   tables `people` and `experts` for the one person named on the case, only for a caller who may see
--   the case's content or for the case's own job (foundation request: move into core/contracts/profile).

set role app_migrator;

alter table app.final_check_runs add column warnings_status text not null default 'done'
  check (warnings_status in ('done', 'not_run'));

create function app.report_named_expert(p_case uuid)
  returns table (person_id uuid, full_name text, registration_body text, registration_number text,
                 hourly_rate numeric, currency text)
  language plpgsql
  stable
  security definer
  set search_path = ''
as $$
begin
  return query
    select c.named_expert_id, p.full_name, e.registration_body, e.registration_number, e.hourly_rate, e.currency
    from app.cases c
    join app.people p on p.id = c.named_expert_id
    left join app.experts e on e.person_id = c.named_expert_id
    where c.id = p_case and app.case_report_input_allowed(p_case, 'case.view_content');
end
$$;
revoke all on function app.report_named_expert(uuid) from public;
grant execute on function app.report_named_expert(uuid) to app_api, app_worker;

reset role;
