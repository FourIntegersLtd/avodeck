-- 0203 down: the verification log goes.

set role app_migrator;

drop table app.fact_verifications cascade;
drop function app.fact_verifications_chain_ok(uuid);
drop function app.fact_verifications_chain();

reset role;
