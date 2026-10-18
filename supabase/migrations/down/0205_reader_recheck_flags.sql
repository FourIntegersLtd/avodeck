-- 0205 down: the pipeline no longer flags facts; its flags go.

set role app_migrator;

drop policy fact_flags_recheck on app.fact_flags;
revoke insert on app.fact_flags from app_worker;
delete from app.fact_flags where person_id is null;
alter table app.fact_flags drop constraint fact_flags_person;
alter table app.fact_flags alter column person_id set not null;

reset role;
