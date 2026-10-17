-- 0110 down: clearer-copy requests go (pages already superseded stay superseded).

set role app_migrator;

drop table app.page_replacements;
drop function app.page_replacements_guard();

reset role;
