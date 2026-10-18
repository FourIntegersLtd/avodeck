-- Down for 0503 write: drops lint issues.
set role app_migrator;
drop table if exists app.lint_issues;
drop function if exists app.lint_issues_guard();
reset role;
