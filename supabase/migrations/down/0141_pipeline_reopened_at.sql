-- 0141 down: drop the reopen-time function.

set role app_migrator;

drop function if exists app.pipeline_reopened_at(uuid);

reset role;
