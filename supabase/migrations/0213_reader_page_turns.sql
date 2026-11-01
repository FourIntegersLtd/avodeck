-- 0213 reader: how a page is turned for reading (`page_turns`, page viewer item 1, approved 2026-10-23).
--
-- A sideways or upside-down page is turned on the screen at once. Only the way it is shown changes:
-- the stored picture, `pages.rotation`, the words, their boxes and every fact stay exactly as they were,
-- and nothing is read again. The screen turns the picture and everything drawn on it together, so the
-- highlights and the word picker still sit over the same words (rule 2).
--
-- One row per page, the latest turn clockwise (0, 90, 180 or 270), set by anyone allowed `page.rotate`
-- (the named expert or an assistant), as themselves. "Upright" is 0; nobody deletes a turn.
-- Like `word_corrections` (0209, decision 0018) it has no `case_id` column: row-level security finds the
-- case through `pages`, and the rows go with their page when a case is deleted.

set role app_migrator;

create table app.page_turns (
  page_id uuid primary key references app.pages (id) on delete cascade,
  degrees int not null check (degrees in (0, 90, 180, 270)),
  person_id uuid not null references app.people (id),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table app.page_turns enable row level security;
revoke all on app.page_turns from app_api, app_worker;  -- the schema's default grants, narrowed below
grant select, insert on app.page_turns to app_api;
grant update (degrees, person_id, updated_at) on app.page_turns to app_api;
grant select on app.page_turns to app_worker;

create policy page_turns_view on app.page_turns for select to app_api
  using (exists (select 1 from app.pages p
                 where p.id = page_id and app.can_access_bundle(p.bundle_id, 'case.view_content')));
create policy page_turns_add on app.page_turns for insert to app_api
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.rotate')));
create policy page_turns_change on app.page_turns for update to app_api
  using (exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.rotate')))
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.rotate')));
create policy page_turns_job on app.page_turns for select to app_worker
  using (exists (select 1 from app.pages p
                 where p.id = page_id
                   and p.bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id())));

reset role;
