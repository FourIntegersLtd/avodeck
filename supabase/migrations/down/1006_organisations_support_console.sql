set role app_migrator;

drop function app.support_case_grants(uuid);
drop function app.support_cases(uuid, text);

reset role;
