-- Down for 0370 setup: drops the conflict answer history.
set role app_migrator;
drop trigger if exists conflict_answers_keep_history on app.conflict_answers;
drop function if exists app.conflict_answers_keep_history();
drop table if exists app.conflict_answer_history;
reset role;
