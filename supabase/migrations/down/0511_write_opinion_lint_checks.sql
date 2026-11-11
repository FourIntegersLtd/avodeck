-- Down for 0511 write: drops the record of whether the model check ran.
set role app_migrator;
drop table if exists app.opinion_lint_checks;
reset role;
