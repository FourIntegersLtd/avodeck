-- 0610 report: section history and locked rows (security audit 16, PV-12 report part; locked-row guard).
--
-- * `report_section_versions`: every body the named expert saved for a report section (sealed under the case
--   key, with its citations, who and when). Filled by a trigger on `report_section_texts`, so no route can
--   forget it; a row is never changed and goes only when its section text goes (the case purge). It has no
--   `case_id`: it follows its section text, like opinion_versions follows its opinion.
-- * `report_section_texts.id`: what a history row points to (the section is still found by case and key).
-- * The rows a build wrote for a locked, served or withdrawn version (`report_paragraphs`, `citations`,
--   `report_snapshots`, `report_builds`) refuse any change or deletion (AVLCK, 409 `locked`), except while the
--   case itself is purged (`app.purging_case_id`). A signed report is what it was signed as (rule 8).

set role app_migrator;

alter table app.report_section_texts
  add column id uuid not null default gen_random_uuid(),
  add constraint report_section_texts_id_key unique (id);

create table app.report_section_versions (
  id uuid primary key default gen_random_uuid(),
  section_text_id uuid not null references app.report_section_texts (id) on delete cascade,
  version_no integer not null check (version_no >= 1),
  body bytea not null,
  citation_refs jsonb not null default '[]' check (jsonb_typeof(citation_refs) = 'array'),
  recorded_by uuid references app.people (id),
  created_at timestamptz not null default now()
);
create index report_section_versions_section on app.report_section_versions (section_text_id, version_no);

create function app.report_section_versions_keep() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  insert into app.report_section_versions (section_text_id, version_no, body, citation_refs, recorded_by)
    values (new.id, new.version, new.body, new.citation_refs, new.updated_by);
  return new;
end
$$;
create trigger report_section_texts_keep_history after insert or update on app.report_section_texts
  for each row execute function app.report_section_versions_keep();

create function app.report_section_versions_append_only() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  if tg_op = 'DELETE' and not exists (select 1 from app.report_section_texts t where t.id = old.section_text_id) then
    return old;
  end if;
  perform app.refuse('report_section_versions is append-only');
  return null;
end
$$;
create trigger report_section_versions_append_only before update or delete on app.report_section_versions
  for each row execute function app.report_section_versions_append_only();

-- What the sections held before this migration is its first history row.
insert into app.report_section_versions (section_text_id, version_no, body, citation_refs, recorded_by, created_at)
  select id, version, body, citation_refs, updated_by, updated_at from app.report_section_texts;

alter table app.report_section_versions enable row level security;
revoke all on app.report_section_versions from app_api, app_worker;
grant select on app.report_section_versions to app_api;
create policy report_section_versions_view on app.report_section_versions for select to app_api
  using (exists (select 1 from app.report_section_texts t
                 where t.id = section_text_id and app.can_access_case(t.case_id, 'case.view_content')));

create function app.report_rows_locked() returns trigger
  language plpgsql
  security definer
  set search_path = ''
as $$
begin
  -- The purge, and restores and support repairs run as the database owner (a login that is not the app's own),
  -- are not refused; anything reached from a session of the app's two roles is, even through a definer function.
  if current_setting('app.purging_case_id', true) is not distinct from old.case_id::text
     or session_user not in ('app_api', 'app_worker') then
    return case tg_op when 'DELETE' then old else new end;
  end if;
  if exists (select 1 from app.report_versions v
             where v.id = old.report_version_id and (v.locked_at is not null or v.status <> 'draft')) then
    perform app.report_locked('the rows of a locked report version cannot be changed');
  end if;
  return case tg_op when 'DELETE' then old else new end;
end
$$;
create trigger report_paragraphs_locked before update or delete on app.report_paragraphs
  for each row execute function app.report_rows_locked();
create trigger citations_locked before update or delete on app.citations
  for each row execute function app.report_rows_locked();
create trigger report_snapshots_locked before update or delete on app.report_snapshots
  for each row execute function app.report_rows_locked();
create trigger report_builds_locked before update or delete on app.report_builds
  for each row execute function app.report_rows_locked();

reset role;
