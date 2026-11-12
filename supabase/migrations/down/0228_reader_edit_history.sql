-- Down 0228: removes the page-work history tables and the flag-history trigger.
set role app_migrator;

drop trigger if exists fact_flags_keep_history on app.fact_flags;
drop function if exists app.fact_flags_keep_history();
drop table if exists app.fact_flag_history;
drop table if exists app.page_changes;
drop table if exists app.page_transcriptions;
drop function if exists app.reader_history_append_only();

reset role;
