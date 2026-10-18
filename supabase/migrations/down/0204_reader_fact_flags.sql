-- 0204 down: fact flags go.

set role app_migrator;

drop table app.fact_flags;

reset role;
