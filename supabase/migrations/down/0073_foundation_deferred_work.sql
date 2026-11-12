-- Undo 0073: work still waiting in the table is lost (it is only an after-commit follow-up).
set role app_migrator;

drop function if exists app.prune_deferred_work(integer);
drop function if exists app.finish_deferred_work(uuid, boolean, text);
drop function if exists app.take_deferred_work(integer);
drop function if exists app.defer_work(uuid, text, text, text, timestamptz);
drop table if exists app.deferred_work;

reset role;
