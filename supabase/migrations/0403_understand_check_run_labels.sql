-- Slice D (audit step 14, D-I2): what each check run counted as already acted on.
-- `check_runs.acted_on` maps a check key to how many entries the software labelled (T-13a) as the action
-- the check looks for. Only checks that ask for labels have a key. Counts and keys only: no words.
set role app_migrator;

alter table app.check_runs add column acted_on jsonb not null default '{}'
  check (jsonb_typeof(acted_on) = 'object');

reset role;
