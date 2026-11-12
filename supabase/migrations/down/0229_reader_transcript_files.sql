-- Down 0229: removes the record of transcript file hashes.
set role app_migrator;

drop table if exists app.transcript_files;
drop function if exists app.transcript_files_append_only();

reset role;
