-- Down 0225: removes the job reference from second readings.
set role app_migrator;

alter table app.second_readings drop column if exists job_id;

reset role;
