-- 0042 foundation: rewrite RLS on pages and documents to use can_access_bundle.
--
-- pages and documents are foundation tables (0001), so this runs here. Every page-child table
-- owned by a later slice (document_scans, page_analysis, ocr_words, page_embeddings,
-- page_quarantines, page_replacements, document_parts, exhibits, record_entries — all created
-- by pipeline migrations 0100+) gets its bundle_id column and RLS rewrite in the pipeline range
-- instead (0127_pipeline_bundle_rls.sql), because migrations apply in strict numeric order and
-- a foundation-numbered migration cannot alter a table that does not exist yet.
--
-- Tables that remain per-case (facts, page_reads, citations, missing_items, record_gaps,
-- second_readings, transcript_views) keep case_id only — they represent one case's reading
-- of the shared bundle.

set role app_migrator;

-- ── Rewrite RLS policies on pages and documents ──────────────────────────

-- pages: drop old (0003 names: pages_view, pages_change, pages_job), create new
drop policy if exists pages_view on app.pages;
drop policy if exists pages_change on app.pages;
drop policy if exists pages_job on app.pages;
create policy pages_view on app.pages for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy pages_change on app.pages for update to app_api
  using (app.can_access_bundle(bundle_id, 'page.rotate'))
  with check (app.can_access_bundle(bundle_id, 'page.rotate'));
create policy pages_job on app.pages to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

-- documents: drop old (0003 names: documents_view, documents_add, documents_change, documents_job)
drop policy if exists documents_view on app.documents;
drop policy if exists documents_add on app.documents;
drop policy if exists documents_change on app.documents;
drop policy if exists documents_job on app.documents;
create policy documents_view on app.documents for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy documents_add on app.documents for insert to app_api
  with check (app.can_access_bundle(bundle_id, 'case.upload'));
create policy documents_change on app.documents for update to app_api
  using (app.can_access_bundle(bundle_id, 'case.upload'))
  with check (app.can_access_bundle(bundle_id, 'case.upload'));
create policy documents_job on app.documents to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

reset role;
