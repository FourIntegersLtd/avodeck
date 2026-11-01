-- 0044 foundation: drop case_id from pages and documents (bundle-structural, foundation tables).
--
-- Now that pages/documents RLS and Python code use bundle_id, the redundant case_id column can
-- be removed. The matching drop on pipeline-owned page-child tables (ocr_words, page_analysis,
-- page_embeddings, document_scans, document_parts, exhibits, record_entries) moves to the
-- pipeline range instead (0129_pipeline_bundle_drop_case_id.sql), because those tables are
-- created by pipeline migrations 0100+, which run after any foundation-numbered migration.
--
-- Tables that KEEP case_id (per-case analysis of a shared bundle):
--   facts, page_reads, fact_verifications, fact_flags, citations, missing_items, record_gaps,
--   second_readings, transcript_views, concordance, page_quarantines (case-scoped analysis),
--   page_replacements (case-scoped request)

set role app_migrator;

drop index if exists app.documents_case;

alter table app.pages drop column case_id;
alter table app.documents drop column case_id;

reset role;
