-- Undo 0039: drop the legal agreement acceptance table (cascades its policies and indexes).
set role app_migrator;

drop table if exists app.legal_agreements;

reset role;
