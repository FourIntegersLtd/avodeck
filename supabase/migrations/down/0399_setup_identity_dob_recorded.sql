-- Down for 0399 setup: drops the date-of-birth-recorded check.
set role app_migrator;
drop function if exists app.case_identity_dob_recorded(uuid);
reset role;
