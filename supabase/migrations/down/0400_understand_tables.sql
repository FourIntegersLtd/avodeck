-- Down for 0400 understand: drops links, overrides, findings, notes, comments and check settings.
set role app_migrator;
drop table if exists app.check_config;
drop table if exists app.comments;
drop table if exists app.consultation_notes;
drop table if exists app.case_guideline_versions;
drop table if exists app.findings;
drop table if exists app.check_runs;
drop table if exists app.availability_overrides;
drop table if exists app.fact_question_links;
drop function if exists app.comments_guard();
drop function if exists app.consultation_notes_guard();
drop function if exists app.findings_guard();
drop function if exists app.fact_question_links_guard();
reset role;
