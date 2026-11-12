-- Undo 0071: no pruning of finished queue rows.
set role app_migrator;

drop function app.prune_queue_rows(integer, integer);

reset role;
