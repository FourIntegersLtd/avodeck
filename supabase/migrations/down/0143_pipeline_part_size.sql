-- 0143 down: drop the part size.

set role app_migrator;

alter table app.document_parts drop column if exists size;

reset role;
