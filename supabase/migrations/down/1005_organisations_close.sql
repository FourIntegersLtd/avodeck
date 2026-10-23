set role app_migrator;

drop function app.close_organisation(uuid);

-- app.memberships_guard() goes back to 0002: re-run that migration's definition of it.

reset role;
