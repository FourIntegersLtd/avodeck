-- Undo 0044: restore documents.case_id, backfilled through the bundle (documents.bundle_id still
-- exists at this point: down/0040 runs later/lower-numbered than this file).
set role app_migrator;

alter table app.documents add column case_id uuid references app.cases (id);

update app.documents d
  set case_id = (select c.id from app.cases c where c.bundle_id = d.bundle_id limit 1)
  where d.case_id is null;

alter table app.documents alter column case_id set not null;

create index documents_case on app.documents (case_id);

reset role;
