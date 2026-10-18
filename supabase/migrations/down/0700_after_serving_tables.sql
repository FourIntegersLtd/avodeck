-- Down for 0700 after serving: drop slice G's tables and functions.
set role app_migrator;

drop function if exists app.after_serving_due_deadlines(date, integer);
drop function if exists app.after_serving_answers_served(uuid, uuid);
drop function if exists app.after_serving_pack_deadline(uuid, text, text, date, text, text, text);
drop function if exists app.after_serving_served_at(uuid);
drop function if exists app.after_serving_served_on(uuid);
drop table if exists app.may_matter;
drop table if exists app.report_changes;
drop function if exists app.report_changes_guard();
drop table if exists app.opposing_claims;
drop function if exists app.opposing_claims_guard();
drop table if exists app.opposing_reports;
drop table if exists app.written_questions;
drop function if exists app.written_questions_guard();
drop table if exists app.written_question_rounds;
drop function if exists app.written_question_rounds_guard();
drop table if exists app.deadline_changes;
drop function if exists app.deadline_changes_guard();
drop table if exists app.deadlines;
drop function if exists app.deadlines_guard();
drop table if exists app.hearings;
drop function if exists app.hearings_guard();
drop table if exists app.court_directions;

reset role;
