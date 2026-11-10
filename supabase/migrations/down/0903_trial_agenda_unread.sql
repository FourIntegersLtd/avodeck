-- Down for 0903 trial: drop the numbers not copied from an agenda version.
set role app_migrator;

alter table app.agenda_versions drop column if exists unread_numbers;

reset role;
