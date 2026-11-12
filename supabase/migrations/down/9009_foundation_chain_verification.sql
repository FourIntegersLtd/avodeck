-- Undo 9009.
set role app_migrator;

drop function if exists app.verify_case_chains(integer);
drop function if exists app.verify_audit_chain(integer);

reset role;
