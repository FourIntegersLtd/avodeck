-- 0118 down: pages no longer record that they were straightened.
set role app_migrator;

alter table app.pages drop column straightened;

reset role;
