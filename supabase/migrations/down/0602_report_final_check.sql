set role app_migrator;
drop function app.report_named_expert(uuid);
alter table app.final_check_runs drop column warnings_status;
reset role;
