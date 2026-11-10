-- Down 0224: removes the prompt version from second readings.
set role app_migrator;

alter table app.second_readings drop column if exists prompt_version;

reset role;
