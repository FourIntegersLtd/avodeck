-- 0137 down: the page-batch functions.

set role app_migrator;

drop function if exists app.pipeline_enter(uuid);
drop function if exists app.pipeline_reopen_run(uuid);
drop function if exists app.pipeline_finish_run(uuid);
drop function if exists app.pipeline_complete_batch(uuid);
drop function if exists app.pipeline_fire_next(uuid);
drop function if exists app.pipeline_topup(uuid);
drop function if exists app.pipeline_has_row(uuid);
drop function if exists app.pipeline_case_busy(uuid, text[]);
drop function if exists app.pipeline_redefer(uuid, boolean);
drop function if exists app.pipeline_defer_row(uuid, text, text, integer);

reset role;
