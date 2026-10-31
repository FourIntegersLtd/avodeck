set role app_migrator;

drop function app.support_cell_status();
drop function app.support_organisation(uuid);
drop function app.support_person(text, integer, interval);

reset role;
