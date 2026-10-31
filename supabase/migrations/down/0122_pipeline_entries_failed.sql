-- 0122 down: the entries-failed marker.

set role app_migrator;

alter table app.page_analysis drop column entries_failed_at;

reset role;
