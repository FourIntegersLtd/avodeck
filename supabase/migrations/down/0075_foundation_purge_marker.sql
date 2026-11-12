-- Undo 0075 (run 9010's down first: the guards call the helper).
set role app_migrator;

drop function if exists app.case_is_purging(uuid);
drop table if exists app.case_purge_marks;

reset role;
