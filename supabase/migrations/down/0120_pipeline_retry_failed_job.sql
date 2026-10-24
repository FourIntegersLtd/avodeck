-- 0120 down: Try again function.

set role app_migrator;

drop function if exists app.requeue_failed_job(uuid, text, integer, boolean);

reset role;
