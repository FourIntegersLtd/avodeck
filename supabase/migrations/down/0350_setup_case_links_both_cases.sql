-- Down for 0350 setup: the link policy checks only the case being linked from again.
set role app_migrator;
drop policy if exists case_links_add on app.case_links;
create policy case_links_add on app.case_links for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup'));
drop function if exists app.cases_share_organisation(uuid, uuid);
reset role;
