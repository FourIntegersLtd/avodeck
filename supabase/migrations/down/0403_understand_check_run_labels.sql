-- Down for 0403 understand check run labels.
set role app_migrator;
alter table app.check_runs drop column if exists acted_on;
reset role;
