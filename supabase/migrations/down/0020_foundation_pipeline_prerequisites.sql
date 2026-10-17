set role app_migrator;

alter table app.uploads drop column append;
delete from app.job_kind_actions where kind in ('scan_document', 'reprocess_case');

reset role;
