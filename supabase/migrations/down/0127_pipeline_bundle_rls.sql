-- Down 0127: drop bundle_id from the page-child tables and restore the case_id-based RLS that
-- 0127 replaced. Run after 0129's down has restored case_id on the tables that lost it.

set role app_migrator;

-- ── 1. Restore case_id-based RLS on page-child tables (API read policies only) ──────────────

-- document_scans
drop policy if exists document_scans_view on app.document_scans;
drop policy if exists document_scans_job on app.document_scans;
create policy document_scans_view on app.document_scans for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy document_scans_job on app.document_scans to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- page_analysis
drop policy if exists page_analysis_view on app.page_analysis;
drop policy if exists page_analysis_job on app.page_analysis;
create policy page_analysis_view on app.page_analysis for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_analysis_job on app.page_analysis to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- page_analysis_review (0105)
drop policy if exists page_analysis_review on app.page_analysis;
create policy page_analysis_review on app.page_analysis for update to app_api
  using (app.can_access_case(case_id, 'page.review_duplicate'))
  with check (app.can_access_case(case_id, 'page.review_duplicate'));

-- ocr_words
drop policy if exists ocr_words_view on app.ocr_words;
drop policy if exists ocr_words_job on app.ocr_words;
create policy ocr_words_view on app.ocr_words for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy ocr_words_job on app.ocr_words to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- page_embeddings
drop policy if exists page_embeddings_view on app.page_embeddings;
drop policy if exists page_embeddings_job_view on app.page_embeddings;
drop policy if exists page_embeddings_job_write on app.page_embeddings;
drop policy if exists page_embeddings_job_update on app.page_embeddings;
create policy page_embeddings_view on app.page_embeddings for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_embeddings_job_view on app.page_embeddings for select to app_worker
  using (case_id = app.current_job_case_id());
create policy page_embeddings_job_write on app.page_embeddings for insert to app_worker
  with check (case_id = app.current_job_case_id());
create policy page_embeddings_job_update on app.page_embeddings for update to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- page_quarantines (case_id was never dropped from this table)
drop policy if exists page_quarantines_view on app.page_quarantines;
drop policy if exists page_quarantines_suggest on app.page_quarantines;
drop policy if exists page_quarantines_resolve on app.page_quarantines;
create policy page_quarantines_view on app.page_quarantines for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_quarantines_suggest on app.page_quarantines for insert to app_api
  with check (app.can_access_case(case_id, 'page.quarantine') and status = 'suggested'
              and suggested_by = app.current_person_id());

-- page_replacements (case_id was never dropped from this table)
drop policy if exists page_replacements_view on app.page_replacements;
drop policy if exists page_replacements_request on app.page_replacements;
drop policy if exists page_replacements_update on app.page_replacements;
create policy page_replacements_view on app.page_replacements for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));

-- document_parts
drop policy if exists document_parts_view on app.document_parts;
drop policy if exists document_parts_job on app.document_parts;
create policy document_parts_view on app.document_parts for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy document_parts_job on app.document_parts to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- exhibits
drop policy if exists exhibits_view on app.exhibits;
drop policy if exists exhibits_job on app.exhibits;
create policy exhibits_view on app.exhibits for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy exhibits_job on app.exhibits to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- record_entries
drop policy if exists record_entries_view on app.record_entries;
drop policy if exists record_entries_job on app.record_entries;
create policy record_entries_view on app.record_entries for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy record_entries_job on app.record_entries to app_worker
  using (case_id = app.current_job_case_id()) with check (case_id = app.current_job_case_id());

-- ── 2. Drop bundle_id from the page-child tables ────────────────────────────

drop index if exists app.document_scans_bundle;
alter table app.document_scans drop column bundle_id;

drop index if exists app.page_analysis_bundle;
alter table app.page_analysis drop column bundle_id;

drop index if exists app.ocr_words_bundle;
alter table app.ocr_words drop column bundle_id;

drop index if exists app.page_embeddings_bundle;
alter table app.page_embeddings drop column bundle_id;

drop index if exists app.page_quarantines_bundle;
alter table app.page_quarantines drop column bundle_id;

drop index if exists app.page_replacements_bundle;
alter table app.page_replacements drop column bundle_id;

drop index if exists app.document_parts_bundle;
alter table app.document_parts drop column bundle_id;

drop index if exists app.exhibits_bundle;
alter table app.exhibits drop column bundle_id;

drop index if exists app.record_entries_bundle;
alter table app.record_entries drop column bundle_id;

reset role;
