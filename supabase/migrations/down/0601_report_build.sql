set role app_migrator;
alter table app.report_settings drop column addressed_contents, drop column created_at;
drop table app.report_builds;
drop table app.report_section_texts;
reset role;
