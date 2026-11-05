-- Undo 0059: the ops metrics functions are gone.
set role app_migrator;

drop function if exists app.ops_queue_stats();
drop function if exists app.ops_job_seconds();

reset role;
