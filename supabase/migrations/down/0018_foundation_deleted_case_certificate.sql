set role app_migrator;

-- app.can_access_case() goes back to 0003: re-run that definition first, then:
drop function app.case_access(uuid, text);

reset role;
