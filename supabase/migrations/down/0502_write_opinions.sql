-- Down for 0502 write: drops opinions and their versions.
set role app_migrator;
drop table if exists app.opinion_versions;
drop table if exists app.opinions;
drop function if exists app.opinions_history();
drop function if exists app.opinions_guard();
reset role;
