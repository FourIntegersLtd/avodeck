-- 0228 reader: who changed what, and the earlier value, for the expert's page work (security audit 16, PV-12).
--
-- Three append-only history tables, none with a `case_id` (they go with their page or fact when a case is
-- deleted, like `page_turns` and `page_notes`):
--   `page_transcriptions`  every typing of an unreadable page, sealed with the case key (the page keeps only
--                          the latest; this keeps every one, with who and when).
--   `page_changes`         every change of a page's relevance or "clearer copy needed", with the old and new value.
--   `fact_flag_history`    a fact flag that was taken off: who set it and when, who removed it and when. Filled
--                          by a trigger on the delete, so no route can forget it.
-- A row is never changed. It may be deleted only when the page (or fact) it belongs to is gone.
-- Who reads them: `page_changes` and `page_transcriptions` keep the earlier values of columns of the shared page row
-- (relevance, clearer copy needed, the typed text), so they go with the page and every case on its bundle reads them,
-- as it reads `ocr_words`. `fact_flag_history` is one case's own work and is read only through that case.

set role app_migrator;

create function app.reader_history_append_only() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and (
       not exists (select 1 from app.pages p where p.id = old.page_id)
       or (tg_table_name = 'fact_flag_history'
           and not exists (select 1 from app.facts f where f.id = old.fact_id))) then
    return old;
  end if;
  perform app.refuse(format('%s is append-only', tg_table_name));
  return null;
end
$$;

create table app.page_transcriptions (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references app.pages (id) on delete cascade,
  person_id uuid not null references app.people (id),
  text bytea not null,
  at timestamptz not null default now()
);
create index page_transcriptions_page on app.page_transcriptions (page_id, at);

create table app.page_changes (
  id uuid primary key default gen_random_uuid(),
  page_id uuid not null references app.pages (id) on delete cascade,
  person_id uuid not null references app.people (id),
  field text not null check (field in ('relevance', 'needs_clearer_copy')),
  old_value text,
  new_value text,
  at timestamptz not null default now()
);
create index page_changes_page on app.page_changes (page_id, at);

create table app.fact_flag_history (
  id uuid primary key default gen_random_uuid(),
  fact_id uuid not null references app.facts (id) on delete cascade,
  page_id uuid not null references app.pages (id) on delete cascade,
  flagged_for text not null,
  flagged_by uuid references app.people (id),
  flagged_at timestamptz not null,
  removed_by uuid references app.people (id),
  at timestamptz not null default now()  -- when the flag was taken off
);
create index fact_flag_history_fact on app.fact_flag_history (fact_id);

create trigger page_transcriptions_append_only before update or delete on app.page_transcriptions
  for each row execute function app.reader_history_append_only();
create trigger page_changes_append_only before update or delete on app.page_changes
  for each row execute function app.reader_history_append_only();
create trigger fact_flag_history_append_only before update or delete on app.fact_flag_history
  for each row execute function app.reader_history_append_only();

-- A flag taken off keeps who set it and who took it off. Not while the facts go with their case, and not
-- when the fact itself is already gone.
create function app.fact_flags_keep_history() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if coalesce(current_setting('app.purging_case_id', true), '') <> '' then
    return old;
  end if;
  insert into app.fact_flag_history (fact_id, page_id, flagged_for, flagged_by, flagged_at, removed_by)
    select f.id, f.page_id, old.flagged_for, old.person_id, old.at, app.current_person_id()
    from app.facts f where f.id = old.fact_id;
  return old;
end
$$;
create trigger fact_flags_keep_history after delete on app.fact_flags
  for each row execute function app.fact_flags_keep_history();

alter table app.page_transcriptions enable row level security;
alter table app.page_changes enable row level security;
alter table app.fact_flag_history enable row level security;
revoke all on app.page_transcriptions, app.page_changes, app.fact_flag_history from app_api, app_worker;
grant select, insert on app.page_transcriptions, app.page_changes to app_api;
grant select on app.fact_flag_history to app_api;

create policy page_transcriptions_view on app.page_transcriptions for select to app_api
  using (exists (select 1 from app.pages p
                 where p.id = page_id and app.can_access_bundle(p.bundle_id, 'case.view_content')));
create policy page_transcriptions_add on app.page_transcriptions for insert to app_api
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.transcribe')));

create policy page_changes_view on app.page_changes for select to app_api
  using (exists (select 1 from app.pages p
                 where p.id = page_id and app.can_access_bundle(p.bundle_id, 'case.view_content')));
create policy page_changes_add on app.page_changes for insert to app_api
  with check (person_id = app.current_person_id()
    and exists (select 1 from app.pages p where p.id = page_id and app.can_access_bundle(p.bundle_id, 'page.transcribe')));

-- A flag is the case's own reading (`fact_flags` and `facts` carry the case), so its history is read through the
-- fact's case: with a bundle shared between cases (ADR 0034) the other case's members do not see it.
create policy fact_flag_history_view on app.fact_flag_history for select to app_api
  using (exists (select 1 from app.facts f
                 where f.id = fact_id and app.can_access_case(f.case_id, 'case.view_content')));

reset role;
