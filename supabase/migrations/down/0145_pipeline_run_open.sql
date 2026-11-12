-- 0145 down: drop the open-run function.

set role app_migrator;

drop function if exists app.pipeline_run_open(uuid);

reset role;
