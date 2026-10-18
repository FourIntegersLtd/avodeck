-- Down for 0504 write: facts no longer mark points and claims stale.
set role app_migrator;
drop trigger if exists write_mark_stale on app.facts;
drop function if exists app.write_mark_stale();
reset role;
