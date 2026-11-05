-- Undo 0062: job changes no longer notify listeners; streams fall back to their timed loop.
set role app_migrator;

drop trigger if exists jobs_notify on app.jobs;
drop function if exists app.jobs_notify();

reset role;
