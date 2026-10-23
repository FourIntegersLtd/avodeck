set role app_migrator;

drop policy memberships_leave on app.memberships;
drop policy memberships_admin_change on app.memberships;
grant update on app.memberships to app_api, app_worker;
drop trigger memberships_actor on app.memberships;
drop function app.memberships_actor();
drop function app.organisation_members(uuid);

reset role;
