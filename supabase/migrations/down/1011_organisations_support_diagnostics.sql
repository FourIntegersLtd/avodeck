set role app_migrator;

drop function app.support_case_diagnostics(uuid);
drop function app.support_code(text);

reset role;
