-- Down for 0396 setup: drops the sealed subject identity.
set role app_migrator;
drop function if exists app.case_identity_recorded(uuid);
drop table if exists app.case_subject_identity;
drop function if exists app.case_subject_identity_touch();
reset role;
