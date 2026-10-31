-- Down 0398: remove the assumption source column.
set role app_migrator;

alter table app.assumptions drop column if exists source_instruction_version_id;

reset role;
