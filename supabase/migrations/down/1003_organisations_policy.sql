set role app_migrator;

drop trigger organisations_policy_guard on app.organisations;
drop function app.organisations_policy_guard();
drop function app.organisation_template_exists(uuid, uuid);

reset role;
