-- Undo 9002: the job no longer counts its stall requeues.
set role app_migrator;

alter table app.jobs drop column if exists stall_requeues;

reset role;
