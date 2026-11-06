-- 0142 down: starting a page-batch phase and reporting its progress.

set role app_migrator;

drop function if exists app.pipeline_report_progress(uuid);
drop function if exists app.pipeline_start_phase(uuid);

reset role;
