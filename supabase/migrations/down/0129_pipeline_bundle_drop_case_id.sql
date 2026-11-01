-- Down 0129: restore case_id on the tables that it was dropped from.
--
-- case_id was redundant with bundle_id (one page's bundle never changes) so the up migration
-- dropped it outright; a bundle can in principle be shared by more than one case, so there is
-- no exact inverse for data already written under 0129. Each row is best-effort backfilled with
-- one case that has access to the row's bundle (the oldest one), which is correct for the common
-- case of one case per bundle and is only approximate when a bundle has been shared since.

set role app_migrator;

-- Drop the bundle_id-only indexes that replaced the case_id ones.
drop index if exists app.record_entries_bundle_date;
drop index if exists app.page_embeddings_bundle_model;

-- Add the case_id columns back.
alter table app.document_scans add column case_id uuid references app.cases (id) on delete cascade;
alter table app.page_analysis add column case_id uuid references app.cases (id) on delete cascade;
alter table app.ocr_words add column case_id uuid references app.cases (id) on delete cascade;
alter table app.page_embeddings add column case_id uuid references app.cases (id) on delete cascade;
alter table app.document_parts add column case_id uuid references app.cases (id) on delete cascade;
alter table app.exhibits add column case_id uuid references app.cases (id) on delete cascade;
alter table app.record_entries add column case_id uuid references app.cases (id) on delete cascade;

-- Best-effort backfill: the oldest case that shares the row's bundle.
update app.document_scans ds set case_id = (
  select c.id from app.cases c where c.bundle_id = ds.bundle_id order by c.created_at limit 1);
update app.page_analysis pa set case_id = (
  select c.id from app.cases c where c.bundle_id = pa.bundle_id order by c.created_at limit 1);
update app.ocr_words ow set case_id = (
  select c.id from app.cases c where c.bundle_id = ow.bundle_id order by c.created_at limit 1);
update app.page_embeddings pe set case_id = (
  select c.id from app.cases c where c.bundle_id = pe.bundle_id order by c.created_at limit 1);
update app.document_parts dp set case_id = (
  select c.id from app.cases c where c.bundle_id = dp.bundle_id order by c.created_at limit 1);
update app.exhibits e set case_id = (
  select c.id from app.cases c where c.bundle_id = e.bundle_id order by c.created_at limit 1);
update app.record_entries re set case_id = (
  select c.id from app.cases c where c.bundle_id = re.bundle_id order by c.created_at limit 1);

alter table app.document_scans alter column case_id set not null;
alter table app.page_analysis alter column case_id set not null;
alter table app.ocr_words alter column case_id set not null;
alter table app.page_embeddings alter column case_id set not null;
alter table app.document_parts alter column case_id set not null;
alter table app.exhibits alter column case_id set not null;
alter table app.record_entries alter column case_id set not null;

-- Re-create the case_id indexes 0129 dropped.
create index document_scans_case on app.document_scans (case_id);
create index page_analysis_case on app.page_analysis (case_id);
create index ocr_words_case on app.ocr_words (case_id);
create index page_embeddings_case on app.page_embeddings (case_id, model);
create index document_parts_case on app.document_parts (case_id);
create index exhibits_case on app.exhibits (case_id);
create index record_entries_case_date on app.record_entries (case_id, event_date nulls last);

reset role;
