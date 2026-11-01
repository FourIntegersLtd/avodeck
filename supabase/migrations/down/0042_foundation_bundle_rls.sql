-- Undo 0042: restore the pre-bundle RLS policies on pages and documents (0003 names and shape),
-- checking case_id/can_access_case directly. documents.case_id is restored by down/0044 and
-- pages.case_id by down/0222, both of which run before this (44 and 222 are both > 42... note
-- down/0044 runs AFTER this one numerically descending, so this file reads case_id columns that
-- are restored by down/0222 (222 > 42, runs first) and by down/0044 (44 > 42, also runs first).
set role app_migrator;

drop policy if exists pages_view on app.pages;
drop policy if exists pages_change on app.pages;
drop policy if exists pages_job on app.pages;
create policy pages_view on app.pages for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy pages_change on app.pages for update to app_api
  using (app.can_access_case(case_id, 'page.rotate'))
  with check (app.can_access_case(case_id, 'page.rotate'));
create policy pages_job on app.pages to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

drop policy if exists documents_view on app.documents;
drop policy if exists documents_add on app.documents;
drop policy if exists documents_change on app.documents;
drop policy if exists documents_job on app.documents;
create policy documents_view on app.documents for select to app_api
  using (app.can_access_case(case_id, 'case.view_content'));
create policy documents_add on app.documents for insert to app_api
  with check (app.can_access_case(case_id, 'case.upload'));
create policy documents_change on app.documents for update to app_api
  using (app.can_access_case(case_id, 'case.upload'))
  with check (app.can_access_case(case_id, 'case.upload'));
create policy documents_job on app.documents to app_worker
  using (case_id = app.current_job_case_id())
  with check (case_id = app.current_job_case_id());

reset role;
