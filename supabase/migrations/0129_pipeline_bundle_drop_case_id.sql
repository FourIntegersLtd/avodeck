-- 0129 pipeline: drop case_id from bundle-owned page-child tables.
--
-- Now that all RLS policies, triggers and Python code use bundle_id, the redundant case_id
-- columns can be removed from the pipeline-owned tables that are structurally part of the
-- bundle. Numbered in the pipeline range because these tables (ocr_words, page_analysis,
-- page_embeddings, document_scans, document_parts, exhibits, record_entries) are created by
-- pipeline migrations 0100-0121, which run after any foundation-numbered migration (0044
-- already dropped case_id from pages and documents, the two foundation page-level tables).
--
-- Tables that KEEP case_id (per-case analysis of a shared bundle):
--   page_quarantines (case-scoped analysis), page_replacements (case-scoped request)

set role app_migrator;

-- Drop the old case_id indexes first (no longer needed; bundle_id indexes replace them)
drop index if exists app.document_scans_case;
drop index if exists app.page_analysis_case;
drop index if exists app.ocr_words_case;
drop index if exists app.page_embeddings_case;  -- named page_embeddings_case in 0113
drop index if exists app.document_parts_case;
drop index if exists app.exhibits_case;
drop index if exists app.record_entries_case_date;

-- Drop case_id columns (cascades drop FKs and remaining constraints referencing them)
alter table app.ocr_words drop column case_id;
alter table app.page_analysis drop column case_id;
alter table app.page_embeddings drop column case_id;
alter table app.document_scans drop column case_id;
alter table app.document_parts drop column case_id;
alter table app.exhibits drop column case_id;
alter table app.record_entries drop column case_id;

-- Re-create the record_entries date index and page_embeddings model index on bundle_id instead
create index record_entries_bundle_date on app.record_entries (bundle_id, event_date nulls last);
create index page_embeddings_bundle_model on app.page_embeddings (bundle_id, model);

reset role;
