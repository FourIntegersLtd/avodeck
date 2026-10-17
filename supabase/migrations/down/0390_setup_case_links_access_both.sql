-- Down for 0390 setup: the 0350 policy again (linked case visible or in the same organisation).
set role app_migrator;
create function app.cases_share_organisation(p_case uuid, p_other uuid) returns boolean
  language sql stable
  security definer
  set search_path = ''
as $$
  select exists (select 1 from app.cases a join app.cases b on b.organisation_id = a.organisation_id
                 where a.id = p_case and b.id = p_other)
$$;
revoke all on function app.cases_share_organisation(uuid, uuid) from public;
grant execute on function app.cases_share_organisation(uuid, uuid) to app_api;
drop policy if exists case_links_add on app.case_links;
create policy case_links_add on app.case_links for insert to app_api
  with check (app.can_access_case(case_id, 'case.edit_setup')
              and (app.can_access_case(linked_case_id, 'case.view_metadata')
                   or app.cases_share_organisation(case_id, linked_case_id)));
reset role;
