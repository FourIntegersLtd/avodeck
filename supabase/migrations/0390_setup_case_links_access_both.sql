-- 0390 setup (slice C): founder decision - a person may link two cases only when they can access both.
-- Replaces the same-organisation allowance of 0350: inserting a link needs `case.edit_setup` on the case and
-- `case.view_metadata` on the linked case, so a link never reveals a case the person cannot see.

set role app_migrator;

drop policy case_links_add on app.case_links;
create policy case_links_add on app.case_links for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup')
              and app.can_access_case(linked_case_id, 'case.view_metadata'));

drop function app.cases_share_organisation(uuid, uuid);

reset role;
