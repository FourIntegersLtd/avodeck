-- Down for 0506 write.
set role app_migrator;

drop table if exists app.witness_comparisons;
drop function if exists app.witness_comparisons_guard();

reset role;
