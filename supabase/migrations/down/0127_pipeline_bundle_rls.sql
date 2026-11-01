-- Undo 0127: restore the pre-bundle, case_id-based RLS on the pipeline page-child tables, and
-- drop their bundle_id columns. By the time this runs, down/0129 has already restored each
-- table's case_id column.
set role app_migrator;

-- document_scans
drop policy if exists document_scans_view on app.document_scans;
drop policy if exists document_scans_job on app.document_scans;
create policy document_scans_view on app.document_scans for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy document_scans_job on app.document_scans to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

-- page_analysis
drop policy if exists page_analysis_view on app.page_analysis;
drop policy if exists page_analysis_job on app.page_analysis;
drop policy if exists page_analysis_review on app.page_analysis;
create policy page_analysis_view on app.page_analysis for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_analysis_job on app.page_analysis to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());
create policy page_analysis_review on app.page_analysis for update to app_api
  using (app.can_access_case(case_id, 'page.review_duplicate'))
  with check (app.can_access_case(case_id, 'page.review_duplicate'));

-- ocr_words
drop policy if exists ocr_words_view on app.ocr_words;
drop policy if exists ocr_words_job on app.ocr_words;
create policy ocr_words_view on app.ocr_words for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy ocr_words_job on app.ocr_words to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

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
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

-- page_quarantines
drop policy if exists page_quarantines_view on app.page_quarantines;
drop policy if exists page_quarantines_suggest on app.page_quarantines;
drop policy if exists page_quarantines_resolve on app.page_quarantines;
create policy page_quarantines_view on app.page_quarantines for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_quarantines_suggest on app.page_quarantines for insert to app_api
  with check (app.can_access_case(case_id, 'page.quarantine') and status = 'suggested'
    and suggested_by = app.current_person_id());
create policy page_quarantines_resolve on app.page_quarantines for update to app_api
  using (app.can_access_case(case_id, 'page.quarantine') and app.can_access_case(case_id, 'fact.verify'))
  with check (app.can_access_case(case_id, 'page.quarantine') and app.can_access_case(case_id, 'fact.verify'));

-- page_replacements
drop policy if exists page_replacements_view on app.page_replacements;
drop policy if exists page_replacements_request on app.page_replacements;
drop policy if exists page_replacements_update on app.page_replacements;
create policy page_replacements_view on app.page_replacements for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy page_replacements_request on app.page_replacements for insert to app_api
  with check (app.can_access_case(case_id, 'page.replace') and requested_by = app.current_person_id());
create policy page_replacements_update on app.page_replacements for update to app_api
  using (app.can_access_case(case_id, 'page.replace'))
  with check (app.can_access_case(case_id, 'page.replace'));

-- document_parts
drop policy if exists document_parts_view on app.document_parts;
drop policy if exists document_parts_job on app.document_parts;
create policy document_parts_view on app.document_parts for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy document_parts_job on app.document_parts to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

-- exhibits
drop policy if exists exhibits_view on app.exhibits;
drop policy if exists exhibits_job on app.exhibits;
create policy exhibits_view on app.exhibits for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy exhibits_job on app.exhibits to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

-- record_entries
drop policy if exists record_entries_view on app.record_entries;
drop policy if exists record_entries_job on app.record_entries;
create policy record_entries_view on app.record_entries for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy record_entries_job on app.record_entries to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

-- drop bundle_id columns and their indexes
drop index if exists app.document_scans_bundle;
drop index if exists app.page_analysis_bundle;
drop index if exists app.ocr_words_bundle;
drop index if exists app.page_embeddings_bundle;
drop index if exists app.page_quarantines_bundle;
drop index if exists app.page_replacements_bundle;
drop index if exists app.document_parts_bundle;
drop index if exists app.exhibits_bundle;
drop index if exists app.record_entries_bundle;

alter table app.document_scans drop column if exists bundle_id;
alter table app.page_analysis drop column if exists bundle_id;
alter table app.ocr_words drop column if exists bundle_id;
alter table app.page_embeddings drop column if exists bundle_id;
alter table app.page_quarantines drop column if exists bundle_id;
alter table app.page_replacements drop column if exists bundle_id;
alter table app.document_parts drop column if exists bundle_id;
alter table app.exhibits drop column if exists bundle_id;
alter table app.record_entries drop column if exists bundle_id;

reset role;
