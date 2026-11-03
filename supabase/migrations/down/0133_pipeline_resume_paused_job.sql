-- 0133 down: hand a paused job back function.

set role app_migrator;

drop function if exists app.resume_paused_job(uuid, text, integer, boolean);

reset role;
