-- 0214 reader: the expert's own bookmarks and private notes (`page_notes`, page viewer batch 2, approved
-- 2026-10-23).
--
-- One row per person and page: a bookmark and a short private note (sealed with the case key). Only the
-- person who keeps them sees, changes or removes them, and only on a case where they may read pages
-- (`page.read`). They are separate from the record: keeping one is not reading the page and changes
-- nothing on it. The worker has no grant at all, so a note never reaches an AI task.
-- Like `page_turns` (0213) and `word_corrections` (0209) it has no `case_id` column: row-level security
-- finds the case through `pages`, and the rows go with their page when a case is deleted.

set role app_migrator;

create table app.page_notes (
  page_id uuid not null references app.pages (id) on delete cascade,
  person_id uuid not null references app.people (id),
  bookmarked boolean not null default false,
  note bytea,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  primary key (page_id, person_id),
  check (bookmarked or note is not null)
);

create index page_notes_person on app.page_notes (person_id);

alter table app.page_notes enable row level security;
revoke all on app.page_notes from app_api, app_worker;  -- the schema's default grants, narrowed below
grant select, insert, delete on app.page_notes to app_api;
grant update (bookmarked, note, updated_at) on app.page_notes to app_api;

create policy page_notes_own on app.page_notes for all to app_api
  using (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.read')))
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.read')));

reset role;
