-- 0116 down: documents no longer say why they could not be opened.
set role app_migrator;

alter table app.documents drop column unreadable_reason;

reset role;
