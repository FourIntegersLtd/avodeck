set role app_migrator;

drop policy organisations_admin_change on app.organisations;
grant update on app.organisations to app_api, app_worker;
drop function app.create_team_organisation(text, text);
drop function app.is_org_admin(uuid);

reset role;
