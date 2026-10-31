-- 0121 down: record entries and the job kind that reads them.

set role app_migrator;

delete from app.job_kind_actions where kind = 'extract_entries';
drop table app.record_entries;
alter table app.page_analysis drop column entries_extracted_at, drop column entries_rejected;

reset role;
