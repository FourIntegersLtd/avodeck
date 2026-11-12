-- Undo 9006: no flags and no caps (the rows added for kinds that had none stay: they only repeat the default action).
set role app_migrator;

update app.job_kind_actions set interactive = null, max_active = null;

reset role;
