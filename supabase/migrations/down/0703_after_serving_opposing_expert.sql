-- Down 0703: drop the other expert's name and role from opposing reports.

set role app_migrator;

alter table app.opposing_reports
  drop column if exists expert_name,
  drop column if exists expert_role;

reset role;
