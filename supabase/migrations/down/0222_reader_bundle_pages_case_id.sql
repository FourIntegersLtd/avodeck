-- Undo 0222: restore pages.case_id, backfilled through the bundle (pages.bundle_id still exists
-- at this point: down/0040, which drops it, runs much later/lower-numbered than this).
set role app_migrator;

alter table app.pages add column case_id uuid references app.cases (id);

update app.pages p
  set case_id = (select c.id from app.cases c where c.bundle_id = p.bundle_id limit 1)
  where p.case_id is null;

alter table app.pages alter column case_id set not null;

reset role;
