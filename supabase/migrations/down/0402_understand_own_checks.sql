-- Down for 0402 understand own checks.
set role app_migrator;
alter table app.check_config drop constraint if exists check_config_custom_has_label;
alter table app.check_config drop column if exists custom;
alter table app.check_config drop column if exists label;
reset role;
