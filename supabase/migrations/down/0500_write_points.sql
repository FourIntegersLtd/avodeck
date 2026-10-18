-- Down for 0500 write: drops points and range notes.
set role app_migrator;
drop table if exists app.range_notes;
drop table if exists app.points;
drop function if exists app.range_notes_guard();
drop function if exists app.points_guard();
reset role;
