-- Undo 0129: restore case_id on the pipeline page-child tables, backfilled through the bundle
-- (bundle_id columns still exist at this point: down/0127 runs after this, down/0040 much later).
set role app_migrator;

drop index if exists app.record_entries_bundle_date;
drop index if exists app.page_embeddings_bundle_model;

alter table app.ocr_words add column case_id uuid references app.cases (id);
alter table app.page_analysis add column case_id uuid references app.cases (id);
alter table app.page_embeddings add column case_id uuid references app.cases (id);
alter table app.document_scans add column case_id uuid references app.cases (id);
alter table app.document_parts add column case_id uuid references app.cases (id);
alter table app.exhibits add column case_id uuid references app.cases (id);
alter table app.record_entries add column case_id uuid references app.cases (id);

update app.ocr_words t set case_id = (select c.id from app.cases c where c.bundle_id = t.bundle_id limit 1);
update app.page_analysis t set case_id = (select c.id from app.cases c where c.bundle_id = t.bundle_id limit 1);
update app.page_embeddings t set case_id = (select c.id from app.cases c where c.bundle_id = t.bundle_id limit 1);
update app.record_entries t set case_id = (select c.id from app.cases c where c.bundle_id = t.bundle_id limit 1);
update app.document_scans t
  set case_id = (select c.id from app.cases c where c.bundle_id = (select d.bundle_id from app.documents d where d.id = t.document_id) limit 1);
update app.document_parts t
  set case_id = (select c.id from app.cases c where c.bundle_id = (select d.bundle_id from app.documents d where d.id = t.parent_document_id) limit 1);
update app.exhibits t
  set case_id = (select c.id from app.cases c where c.bundle_id = (select d.bundle_id from app.documents d where d.id = t.document_id) limit 1);

alter table app.ocr_words alter column case_id set not null;
alter table app.page_analysis alter column case_id set not null;
alter table app.page_embeddings alter column case_id set not null;
alter table app.document_scans alter column case_id set not null;
alter table app.document_parts alter column case_id set not null;
alter table app.exhibits alter column case_id set not null;
alter table app.record_entries alter column case_id set not null;

create index document_scans_case on app.document_scans (case_id);
create index page_analysis_case on app.page_analysis (case_id);
create index ocr_words_case on app.ocr_words (case_id);
create index page_embeddings_case on app.page_embeddings (case_id);
create index document_parts_case on app.document_parts (case_id);
create index exhibits_case on app.exhibits (case_id);
create index record_entries_case_date on app.record_entries (case_id, event_date nulls last);

reset role;
