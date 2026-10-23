set role app_migrator;

drop function app.support_organisation_closed(uuid);

reset role;
