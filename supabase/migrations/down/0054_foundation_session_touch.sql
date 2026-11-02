-- Down 0054: activity is counted inside the session lookup again (0053).
set role app_migrator;
-- Revert the code too (core/db.py counts activity with session_touch): put back 0053's p_touch lookup first.
drop function if exists app.session_touch(uuid, interval, interval);
reset role;
