-- 0134 down: the orphaned-jobs sweep function.

set role app_migrator;
drop function if exists app.requeue_orphaned_jobs(integer, double precision);
reset role;
