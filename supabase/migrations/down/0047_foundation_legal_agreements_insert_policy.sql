-- Undo 0047: drop the legal_agreements insert policy.
set role app_migrator;

drop policy if exists legal_agreements_insert on app.legal_agreements;

reset role;
