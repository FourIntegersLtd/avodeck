-- Down for 0501 write: drops witness claims.
set role app_migrator;
drop table if exists app.witness_claims;
drop function if exists app.witness_claims_guard();
reset role;
