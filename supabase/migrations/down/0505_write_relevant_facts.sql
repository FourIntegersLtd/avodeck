-- Down for 0505 write.
set role app_migrator;

drop policy if exists opinions_job_read on app.opinions;
drop table if exists app.relevant_facts;
drop function if exists app.relevant_facts_guard();

reset role;
