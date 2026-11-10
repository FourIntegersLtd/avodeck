-- 0705 after serving: a claim the check left unchecked can be settled by the named expert, who then becomes
-- the one who checked it. `checked_by` gains 'expert' (the person is already kept in `confirmed_by`).

set role app_migrator;

alter table app.opposing_claims
  drop constraint if exists opposing_claims_checked_by_check;

alter table app.opposing_claims
  add constraint opposing_claims_checked_by_check check (checked_by in ('code', 'model', 'expert'));

reset role;
