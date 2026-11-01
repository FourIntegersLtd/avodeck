-- Undo 0221: restore the page-child RLS policies that found their case through pages.case_id
-- directly (pages.case_id is restored by down/0222, which runs before this).
set role app_migrator;

drop policy if exists word_corrections_view on app.word_corrections;
create policy word_corrections_view on app.word_corrections for select to app_api
  using (exists (select 1 from app.pages p
                 where p.id = page_id and app.can_access_case(p.case_id, 'case.view_content')));

drop policy if exists word_corrections_add on app.word_corrections;
create policy word_corrections_add on app.word_corrections for insert to app_api
  with check (person_id = app.current_person_id() and superseded_at is null
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_case(p.case_id, 'fact.verify')));

drop policy if exists word_corrections_replace on app.word_corrections;
create policy word_corrections_replace on app.word_corrections for update to app_api
  using (superseded_at is null
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_case(p.case_id, 'fact.verify')))
  with check (superseded_at is not null);

drop policy if exists word_corrections_job on app.word_corrections;
create policy word_corrections_job on app.word_corrections for select to app_worker
  using (exists (select 1 from app.pages p
                 where p.id = page_id and p.case_id = app.current_job_case_id()));

drop policy if exists word_corrections_words_replaced on app.word_corrections;
create policy word_corrections_words_replaced on app.word_corrections for update to app_worker
  using (superseded_at is null
    and exists (select 1 from app.pages p where p.id = page_id and p.case_id = app.current_job_case_id()))
  with check (superseded_at is not null);

drop policy if exists page_turns_view on app.page_turns;
create policy page_turns_view on app.page_turns for select to app_api
  using (exists (select 1 from app.pages p
                 where p.id = page_id and app.can_access_case(p.case_id, 'case.view_content')));

drop policy if exists page_turns_add on app.page_turns;
create policy page_turns_add on app.page_turns for insert to app_api
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_case(p.case_id, 'page.rotate')));

drop policy if exists page_turns_change on app.page_turns;
create policy page_turns_change on app.page_turns for update to app_api
  using (exists (select 1 from app.pages p where p.id = page_id and app.can_access_case(p.case_id, 'page.rotate')))
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_case(p.case_id, 'page.rotate')));

drop policy if exists page_turns_job on app.page_turns;
create policy page_turns_job on app.page_turns for select to app_worker
  using (exists (select 1 from app.pages p
                 where p.id = page_id and p.case_id = app.current_job_case_id()));

drop policy if exists page_notes_own on app.page_notes;
create policy page_notes_own on app.page_notes for all to app_api
  using (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_case(p.case_id, 'page.read')))
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_case(p.case_id, 'page.read')));

reset role;
