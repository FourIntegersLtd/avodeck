-- Down 0223: removes the export_transcript_pdf job kind.
set role app_migrator;

delete from app.job_kind_actions where kind = 'export_transcript_pdf';

reset role;
