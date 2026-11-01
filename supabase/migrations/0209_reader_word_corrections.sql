-- 0209 reader: the expert's reading of misread words (`word_corrections`, decision 0018, rule 2).
--
-- When the computer misread some words (often handwriting), the named expert types what those words
-- really say while changing a fact. The computer's reading stays in `ocr_words` untouched; the expert's
-- reading of that run of words is kept here, sealed with the case key like every word. A fact's value
-- must still be found in its cited words, with the expert's reading in place of those words.
-- A later reading of any of the same words replaces the earlier one: the earlier row gets
-- `superseded_at` (once) and is otherwise never changed or deleted by the running app.
--
-- A correction belongs to a page and has no `case_id` column (decision 0018): later migrations
-- (0380-0799) set the purge order with fixed lists, so a table added here could not stay in it. Row-level
-- security finds the case through `pages`, and the rows go with their page when a case is deleted.

set role app_migrator;

create table app.word_corrections (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references app.pages (id) on delete cascade,
  word_ids text[] not null check (cardinality(word_ids) between 1 and 50),
  text bytea not null,
  person_id uuid not null references app.people (id),
  created_at timestamptz not null default now(),
  superseded_at timestamptz
);
create index word_corrections_current on app.word_corrections (page_id) where superseded_at is null;

alter table app.word_corrections enable row level security;
revoke all on app.word_corrections from app_api, app_worker;  -- the schema's default grants, narrowed below
grant select, insert on app.word_corrections to app_api;
grant update (superseded_at) on app.word_corrections to app_api;
grant select on app.word_corrections to app_worker;

create policy word_corrections_view on app.word_corrections for select to app_api
  using (exists (select 1 from app.pages p
                 where p.id = page_id and app.can_access_bundle(p.bundle_id, 'case.view_content')));
create policy word_corrections_add on app.word_corrections for insert to app_api
  with check (person_id = app.current_person_id() and superseded_at is null
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'fact.verify')));
create policy word_corrections_replace on app.word_corrections for update to app_api
  using (superseded_at is null
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'fact.verify')))
  with check (superseded_at is not null);
create policy word_corrections_job on app.word_corrections for select to app_worker
  using (exists (select 1 from app.pages p
                 where p.id = page_id
                   and p.bundle_id = (select c.bundle_id from app.cases c where c.id = app.current_job_case_id())));

reset role;
