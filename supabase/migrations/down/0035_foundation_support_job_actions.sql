-- Undo 0035: support's job actions.
set role app_migrator;

drop function if exists app.support_job_action(uuid, text, text, text);
drop table if exists app.job_support_actions;

reset role;
