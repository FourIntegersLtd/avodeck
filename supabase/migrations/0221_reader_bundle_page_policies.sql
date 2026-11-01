-- 0221 reader: redefine the page-child RLS policies that still checked `pages.case_id` directly, now
-- that pages carries `bundle_id` only (0044). `word_corrections`, `page_turns` and `page_notes` have no
-- `case_id` of their own (0209, 0213, 0214) and found the case through `pages`; they now find the bundle
-- through `pages` instead, via `can_access_bundle` or the job's case's bundle.

set role app_migrator;

drop policy word_corrections_view on app.word_corrections;
create policy word_corrections_view on app.word_corrections for select to app_api
  using (exists (select 1 from app.pages p
                 where p.id = page_id and app.can_access_bundle(p.bundle_id, 'case.view_content')));

drop policy word_corrections_add on app.word_corrections;
create policy word_corrections_add on app.word_corrections for insert to app_api
  with check (person_id = app.current_person_id() and superseded_at is null
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'fact.verify')));

drop policy word_corrections_replace on app.word_corrections;
create policy word_corrections_replace on app.word_corrections for update to app_api
  using (superseded_at is null
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'fact.verify')))
  with check (superseded_at is not null);

drop policy word_corrections_job on app.word_corrections;
create policy word_corrections_job on app.word_corrections for select to app_worker
  using (exists (select 1 from app.pages p
                 where p.id = page_id
                   and p.bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id())));

drop policy word_corrections_words_replaced on app.word_corrections;
create policy word_corrections_words_replaced on app.word_corrections for update to app_worker
  using (superseded_at is null
    and exists (select 1 from app.pages p
                where p.id = page_id
                  and p.bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id())))
  with check (superseded_at is not null);

drop policy page_turns_view on app.page_turns;
create policy page_turns_view on app.page_turns for select to app_api
  using (exists (select 1 from app.pages p
                 where p.id = page_id and app.can_access_bundle(p.bundle_id, 'case.view_content')));

drop policy page_turns_add on app.page_turns;
create policy page_turns_add on app.page_turns for insert to app_api
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.rotate')));

drop policy page_turns_change on app.page_turns;
create policy page_turns_change on app.page_turns for update to app_api
  using (exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.rotate')))
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.rotate')));

drop policy page_turns_job on app.page_turns;
create policy page_turns_job on app.page_turns for select to app_worker
  using (exists (select 1 from app.pages p
                 where p.id = page_id
                   and p.bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id())));

drop policy page_notes_own on app.page_notes;
create policy page_notes_own on app.page_notes for all to app_api
  using (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.read')))
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.read')));

reset role;
