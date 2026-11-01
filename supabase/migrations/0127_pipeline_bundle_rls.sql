-- 0127 pipeline: add bundle_id to page-child tables and rewrite their RLS to use can_access_bundle.
--
-- Carries forward the bundle split (ADR 0034, foundation 0040-0042) onto the tables pipeline owns.
-- These tables are structurally part of the bundle (their content belongs to the bundle's pages,
-- not to any one case's analysis of those pages), so they gain a bundle_id column and their RLS
-- policies switch from can_access_case(case_id, ...) to can_access_bundle(bundle_id, ...).
--
-- Numbered in the pipeline range (not foundation) because these tables (document_scans,
-- page_analysis, ocr_words, page_embeddings, page_quarantines, page_replacements, document_parts,
-- exhibits, record_entries) are created by pipeline migrations 0100-0121, which run after any
-- foundation-numbered migration.
--
-- The worker policies stay on case_id via current_job_case_id() because the worker always
-- runs in the context of a specific case's job.

set role app_migrator;

-- ── 1. Add bundle_id to bundle-owned page-child tables ──────────────────────

-- document_scans
alter table app.document_scans add column bundle_id uuid references app.bundles (id);
update app.document_scans ds set bundle_id = (select d.bundle_id from app.documents d where d.id = ds.document_id);
alter table app.document_scans alter column bundle_id set not null;
create index document_scans_bundle on app.document_scans (bundle_id);

-- page_analysis
alter table app.page_analysis add column bundle_id uuid references app.bundles (id);
update app.page_analysis pa set bundle_id = (select p.bundle_id from app.pages p where p.id = pa.page_id);
alter table app.page_analysis alter column bundle_id set not null;
create index page_analysis_bundle on app.page_analysis (bundle_id);

-- ocr_words
alter table app.ocr_words add column bundle_id uuid references app.bundles (id);
update app.ocr_words ow set bundle_id = (select p.bundle_id from app.pages p where p.id = ow.page_id);
alter table app.ocr_words alter column bundle_id set not null;
create index ocr_words_bundle on app.ocr_words (bundle_id);

-- page_embeddings
alter table app.page_embeddings add column bundle_id uuid references app.bundles (id);
update app.page_embeddings pe set bundle_id = (select p.bundle_id from app.pages p where p.id = pe.page_id);
alter table app.page_embeddings alter column bundle_id set not null;
create index page_embeddings_bundle on app.page_embeddings (bundle_id);

-- page_quarantines
alter table app.page_quarantines add column bundle_id uuid references app.bundles (id);
update app.page_quarantines pq set bundle_id = (select p.bundle_id from app.pages p where p.id = pq.page_id);
alter table app.page_quarantines alter column bundle_id set not null;
create index page_quarantines_bundle on app.page_quarantines (bundle_id);

-- page_replacements
alter table app.page_replacements add column bundle_id uuid references app.bundles (id);
update app.page_replacements pr set bundle_id = (select p.bundle_id from app.pages p where p.id = pr.page_id);
alter table app.page_replacements alter column bundle_id set not null;
create index page_replacements_bundle on app.page_replacements (bundle_id);

-- document_parts
alter table app.document_parts add column bundle_id uuid references app.bundles (id);
update app.document_parts dp set bundle_id = (select d.bundle_id from app.documents d where d.id = dp.parent_document_id);
alter table app.document_parts alter column bundle_id set not null;
create index document_parts_bundle on app.document_parts (bundle_id);

-- exhibits
alter table app.exhibits add column bundle_id uuid references app.bundles (id);
update app.exhibits e set bundle_id = (select d.bundle_id from app.documents d where d.id = e.document_id);
alter table app.exhibits alter column bundle_id set not null;
create index exhibits_bundle on app.exhibits (bundle_id);

-- record_entries (bundle-level: a page's records, not a case's analysis)
alter table app.record_entries add column bundle_id uuid references app.bundles (id);
update app.record_entries re set bundle_id = (select p.bundle_id from app.pages p where p.id = re.page_id);
alter table app.record_entries alter column bundle_id set not null;
create index record_entries_bundle on app.record_entries (bundle_id);

-- ── 2. Rewrite RLS on page-child tables (API read policies only; worker stays on case_id) ────

-- document_scans
drop policy if exists document_scans_view on app.document_scans;
drop policy if exists document_scans_job on app.document_scans;
create policy document_scans_view on app.document_scans for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy document_scans_job on app.document_scans to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

-- page_analysis
drop policy if exists page_analysis_view on app.page_analysis;
drop policy if exists page_analysis_job on app.page_analysis;
create policy page_analysis_view on app.page_analysis for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy page_analysis_job on app.page_analysis to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

-- page_analysis_review (0105): the duplicate-review update policy, also switched to bundle_id
drop policy if exists page_analysis_review on app.page_analysis;
create policy page_analysis_review on app.page_analysis for update to app_api
  using (app.can_access_bundle(bundle_id, 'page.review_duplicate'))
  with check (app.can_access_bundle(bundle_id, 'page.review_duplicate'));

-- ocr_words
drop policy if exists ocr_words_view on app.ocr_words;
drop policy if exists ocr_words_job on app.ocr_words;
create policy ocr_words_view on app.ocr_words for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy ocr_words_job on app.ocr_words to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

-- page_embeddings
drop policy if exists page_embeddings_view on app.page_embeddings;
drop policy if exists page_embeddings_job_view on app.page_embeddings;
drop policy if exists page_embeddings_job_write on app.page_embeddings;
drop policy if exists page_embeddings_job_update on app.page_embeddings;
create policy page_embeddings_view on app.page_embeddings for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy page_embeddings_job_view on app.page_embeddings for select to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));
create policy page_embeddings_job_write on app.page_embeddings for insert to app_worker
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));
create policy page_embeddings_job_update on app.page_embeddings for update to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

-- page_quarantines (has multiple policies)
drop policy if exists page_quarantines_view on app.page_quarantines;
drop policy if exists page_quarantines_suggest on app.page_quarantines;
drop policy if exists page_quarantines_resolve on app.page_quarantines;
create policy page_quarantines_view on app.page_quarantines for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy page_quarantines_suggest on app.page_quarantines for insert to app_api
  with check (app.can_access_bundle(bundle_id, 'page.quarantine') and status = 'suggested'
    and suggested_by = app.current_person_id());
create policy page_quarantines_resolve on app.page_quarantines for update to app_api
  using (app.can_access_bundle(bundle_id, 'page.quarantine') and app.can_access_bundle(bundle_id, 'fact.verify'))
  with check (app.can_access_bundle(bundle_id, 'page.quarantine') and app.can_access_bundle(bundle_id, 'fact.verify'));

-- page_replacements (has view, request, update policies)
drop policy if exists page_replacements_view on app.page_replacements;
drop policy if exists page_replacements_request on app.page_replacements;
drop policy if exists page_replacements_update on app.page_replacements;
create policy page_replacements_view on app.page_replacements for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy page_replacements_request on app.page_replacements for insert to app_api
  with check (app.can_access_bundle(bundle_id, 'page.replace') and requested_by = app.current_person_id());
create policy page_replacements_update on app.page_replacements for update to app_api
  using (app.can_access_bundle(bundle_id, 'page.replace'))
  with check (app.can_access_bundle(bundle_id, 'page.replace'));

-- document_parts
drop policy if exists document_parts_view on app.document_parts;
drop policy if exists document_parts_job on app.document_parts;
create policy document_parts_view on app.document_parts for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy document_parts_job on app.document_parts to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

-- exhibits
drop policy if exists exhibits_view on app.exhibits;
drop policy if exists exhibits_job on app.exhibits;
create policy exhibits_view on app.exhibits for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy exhibits_job on app.exhibits to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

-- record_entries
drop policy if exists record_entries_view on app.record_entries;
drop policy if exists record_entries_job on app.record_entries;
create policy record_entries_view on app.record_entries for select to app_api
  using (app.can_access_bundle(bundle_id, 'case.view_content'));
create policy record_entries_job on app.record_entries to app_worker
  using (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()))
  with check (bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id()));

reset role;
