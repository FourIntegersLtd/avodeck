-- Undo 0058: the job no longer remembers a stall.
set role app_migrator;

alter table app.jobs drop column if exists requeued_stall;

reset role;
