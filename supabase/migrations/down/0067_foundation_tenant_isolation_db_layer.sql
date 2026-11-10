-- Undo 0067: app_api may again set or change a case's bundle and organisation, name any case in an audit row,
-- and insert its own people row, change its own email or reopen a closed account.
set role app_migrator;

drop trigger people_identity_columns_insert on app.people;
drop trigger people_identity_columns on app.people;
drop function app.people_identity_columns();

drop policy audit_log_write on app.audit_log;
create policy audit_log_write on app.audit_log for insert to app_api
  with check (person_id = app.current_person_id());
drop function if exists app.person_took_part_in_case(uuid);
drop function if exists app.person_belongs_or_belonged_to_org(uuid);

drop trigger cases_0_tenant_columns_insert on app.cases;
drop trigger cases_tenant_columns on app.cases;
drop function app.cases_tenant_columns();

reset role;
