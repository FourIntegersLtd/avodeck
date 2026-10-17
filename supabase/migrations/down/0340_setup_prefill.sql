-- Down for 0340 setup: drops the prefill suggestions and runs.
set role app_migrator;
drop table if exists app.prefill_runs;
drop table if exists app.prefill_suggestions;
drop function if exists app.prefill_suggestions_guard();
reset role;
