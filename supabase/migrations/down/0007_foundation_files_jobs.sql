-- Down for 0007 foundation files, job queue, scheduled runs and transcripts.
drop function if exists app.last_scheduled_run(text), app.claim_scheduled_run(text, date),
  app.enqueue_job(uuid, text, text, integer, boolean, text, text);
drop table if exists app.transcripts, app.scheduled_runs, app.job_steps, app.uploads;
drop function if exists app.uploads_guard();
alter table app.jobs drop constraint if exists jobs_error_code_code, drop constraint if exists jobs_step_code;
alter table app.jobs drop column if exists step;
drop schema if exists procrastinate cascade;
