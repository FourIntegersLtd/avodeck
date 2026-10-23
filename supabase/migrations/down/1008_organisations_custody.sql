set role app_migrator;

drop function app.support_case_custodian(uuid);

reset role;
