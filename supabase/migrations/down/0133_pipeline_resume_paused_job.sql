-- 0133 down: hand a paused job back function.

set role app_migrator;

drop function if exists app.job_waits_in_queue(uuid);
drop function if exists app.resume_paused_job(uuid, text);

reset role;
