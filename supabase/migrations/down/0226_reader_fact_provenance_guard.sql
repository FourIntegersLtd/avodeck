-- Down 0226: removes the check that a fact cites its own page's words and changes only with a check-log row.
set role app_migrator;

drop trigger if exists facts_provenance on app.facts;
drop function if exists app.facts_provenance_check();

reset role;
