-- Down 0054: activity is counted inside the session lookup again (0053).
set role app_migrator;
drop function if exists app.session_touch(uuid);
reset role;
