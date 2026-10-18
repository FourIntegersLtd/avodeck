-- Down for 0114: availability evidence removed.
set role app_migrator;

alter table app.page_analysis drop column if exists availability_evidence;

reset role;
