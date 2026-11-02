-- 0223 reader: a PDF copy of a transcript is an export job of its own kind (slice B), allowed by the same action as the .docx copy (0215).

set role app_migrator;

insert into app.job_kind_actions (kind, action) values ('export_transcript_pdf', 'case.export');

reset role;
