-- Down for 0310 setup: drops the case setup tables and their functions.
set role app_migrator;
drop table if exists app.case_subject_keys;
drop table if exists app.case_instructions;
drop table if exists app.case_links;
drop table if exists app.case_instructing_parties;
drop table if exists app.case_parties;
drop function if exists app.case_subject_keys_touch();
drop function if exists app.case_parties_guard();
reset role;
