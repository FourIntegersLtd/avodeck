-- Undo 0063: drop the two hot-query indexes; the queries still work, only slower.
set role app_migrator;

drop index if exists app.jobs_case_created_idx;
drop index if exists app.pages_bundle_document_idx;

reset role;
