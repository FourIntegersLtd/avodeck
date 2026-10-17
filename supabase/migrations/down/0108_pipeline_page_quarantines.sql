-- 0108 down: quarantine suggestions and decisions go.

set role app_migrator;

drop table app.page_quarantines;
drop function app.page_quarantines_guard();

reset role;
