-- 0207 down: the job no longer logs re-mapped facts. The log is append-only, so this refuses while
-- any `remapped` row exists (the chain would break if they were removed).

set role app_migrator;

drop policy fact_verifications_remapped on app.fact_verifications;
revoke insert on app.fact_verifications from app_worker;
alter table app.fact_verifications drop constraint fact_verifications_person;
alter table app.fact_verifications alter column person_id set not null;
alter table app.fact_verifications drop constraint fact_verifications_action_check;
alter table app.fact_verifications add constraint fact_verifications_action_check
  check (action in ('correct', 'not_right', 'edit', 'move'));

reset role;
