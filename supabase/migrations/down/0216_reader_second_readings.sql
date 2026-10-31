-- Down 0216: remove the second readings.
set role app_migrator;
delete from app.job_kind_actions where kind = 'second_reading';
drop table if exists app.second_readings;
drop function if exists app.second_readings_same_case();
reset role;
