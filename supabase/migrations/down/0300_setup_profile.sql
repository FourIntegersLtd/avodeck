-- Down for 0300 setup: drops the expert profile tables and their functions.
set role app_migrator;
drop table if exists app.signatures;
drop table if exists app.declarations;
drop table if exists app.report_templates;
drop table if exists app.expert_training;
drop table if exists app.expert_roles;
drop function if exists app.report_templates_revision();
drop function if exists app.setup_bump_version();
drop function if exists app.declarations_guard();
drop function if exists app.expert_has_served(uuid);
reset role;
