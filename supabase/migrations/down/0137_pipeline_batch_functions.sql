-- 0137 down: the page-batch functions.

set role app_migrator;

drop function if exists app.pipeline_case_busy(uuid, text[]);
drop function if exists app.pipeline_redefer(uuid, boolean);
drop function if exists app.pipeline_defer_row(uuid, text, text, integer);

reset role;
