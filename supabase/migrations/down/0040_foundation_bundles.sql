-- Undo 0040: remove the bundle entity. By the time this runs, every later migration that added
-- its own bundle_id column (pipeline 0127, this file's own cases/documents/pages columns) has
-- already had its down applied, so nothing but this migration's own columns and table remain.
set role app_migrator;

drop trigger if exists bundles_no_orphan_delete on app.bundles;
drop function if exists app.bundles_guard();
drop trigger if exists cases_auto_bundle on app.cases;
drop function if exists app.cases_create_bundle();

drop index if exists app.pages_bundle;
drop index if exists app.documents_bundle;
drop index if exists app.cases_bundle;

alter table app.pages drop column if exists bundle_id;
alter table app.documents drop column if exists bundle_id;
alter table app.cases drop column if exists bundle_id;

drop table if exists app.bundles;

reset role;
