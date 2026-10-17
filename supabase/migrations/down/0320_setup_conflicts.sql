-- Down for 0320 setup: drops conflict answers, declarations, withdrawals and the coverage check.
set role app_migrator;
drop function if exists app.expert_covers_period(uuid, text[]);
drop table if exists app.case_withdrawals;
drop table if exists app.conflict_declarations;
drop table if exists app.conflict_answers;
reset role;
