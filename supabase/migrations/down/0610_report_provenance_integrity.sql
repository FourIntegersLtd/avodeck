-- Down for 0610 report: drops the section history and the locked-row guards.
set role app_migrator;
drop trigger if exists report_paragraphs_locked on app.report_paragraphs;
drop trigger if exists citations_locked on app.citations;
drop trigger if exists report_snapshots_locked on app.report_snapshots;
drop trigger if exists report_builds_locked on app.report_builds;
drop function if exists app.report_rows_locked();
drop trigger if exists report_section_texts_keep_history on app.report_section_texts;
drop table if exists app.report_section_versions;
drop function if exists app.report_section_versions_keep();
drop function if exists app.report_section_versions_append_only();
alter table app.report_section_texts drop constraint if exists report_section_texts_id_key, drop column if exists id;
reset role;
